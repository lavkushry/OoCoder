#!/usr/bin/env node
import { Command } from "commander";
import {
  checkAuth,
  closeDb,
  discoverIssues,
  discussIssue,
  followUpCiFixes,
  initOoCoder,
  listCandidates,
  loadAllowlist,
  loadPolicy,
  workOnIssue,
  writeDashboard,
  type RuntimeMode,
} from "@oocoder/core";

const program = new Command();

program
  .name("oocoder")
  .description("Autonomous OSS contribution agent")
  .version("0.2.0");

program
  .command("init")
  .description("Create ~/.oocoder, copy configs, and check auth")
  .action(async () => {
    const result = await initOoCoder();
    console.log(`Home: ${result.home}${result.created ? " (created)" : ""}`);
    console.log(`Auth: ${result.auth.message}`);
    if (result.auth.ghLogin) console.log(`GitHub: ${result.auth.ghLogin}`);
    console.log(
      `Allowlist: ${result.allowlistRepos} repos, ${result.allowlistOrgs} orgs`,
    );
    console.log(`Policy labels: ${result.policyLabels.join(", ")}`);
    if (result.allowlistRepos === 0 && result.allowlistOrgs === 0) {
      console.log(
        "\nAdd repos to ~/.oocoder/allowlist.yaml before running work/discuss.",
      );
    }
  });

program
  .command("discover")
  .description("Search GitHub for contribution candidates")
  .option("-l, --label <labels...>", "Issue labels to search")
  .option("--lang <language>", "Filter by language")
  .option("-n, --limit <n>", "Max candidates", "15")
  .option("--query <extra>", "Extra GitHub search terms")
  .action(async (opts: {
    label?: string[];
    lang?: string;
    limit: string;
    query?: string;
  }) => {
    await initOoCoder();
    const issues = await discoverIssues({
      labels: opts.label,
      language: opts.lang,
      limit: Number(opts.limit),
      queryExtra: opts.query,
    });
    if (issues.length === 0) {
      console.log("No candidates found (check allowlist + labels).");
      return;
    }
    for (const issue of issues) {
      console.log(
        `[${issue.score.toFixed(0).padStart(2)}] ${issue.owner}/${issue.repo}#${issue.number}  ${issue.title}`,
      );
      console.log(`       ${issue.url}`);
    }
  });

program
  .command("discuss")
  .description("Post an investigating comment on an issue (with disclosure)")
  .argument("<issue>", "Issue URL or owner/repo#number")
  .option("--dry-run", "Print comment without posting", false)
  .option("-m, --message <text>", "Custom comment body")
  .action(async (issue: string, opts: { dryRun?: boolean; message?: string }) => {
    await initOoCoder();
    const result = await discussIssue(issue, {
      dryRun: opts.dryRun,
      message: opts.message,
    });
    if (result.dryRun) {
      console.log("--- dry-run comment ---");
      console.log(result.body);
      return;
    }
    console.log(`Posted: ${result.commentUrl}`);
  });

program
  .command("work")
  .description("Implement a fix for an issue and open a PR")
  .argument("<issue>", "Issue URL or owner/repo#number")
  .option("--dry-run", "Prepare / analyze without coding or posting", false)
  .option("--skip-pr", "Commit locally but do not open a PR", false)
  .option("--skip-discuss", "Do not post an issue comment first", false)
  .option("--runtime <mode>", "local | cloud", "local")
  .option("--model <id>", "Cursor model id", "composer-2.5")
  .action(
    async (
      issue: string,
      opts: {
        dryRun?: boolean;
        skipPr?: boolean;
        skipDiscuss?: boolean;
        runtime: string;
        model: string;
      },
    ) => {
      await initOoCoder();
      const runtime = opts.runtime as RuntimeMode;
      if (runtime !== "local" && runtime !== "cloud") {
        throw new Error(`Invalid --runtime: ${opts.runtime}`);
      }
      const result = await workOnIssue({
        issueUrl: issue,
        dryRun: opts.dryRun,
        skipPr: opts.skipPr,
        skipDiscuss: opts.skipDiscuss,
        runtime,
        model: opts.model,
      });
      console.log(result.message);
      if (result.worktreePath) console.log(`Worktree: ${result.worktreePath}`);
      if (result.branch) console.log(`Branch: ${result.branch}`);
      if (result.prUrl) console.log(`PR: ${result.prUrl}`);
      if (result.agentId) console.log(`Agent: ${result.agentId}`);
      console.log(
        `Diff: ${result.filesChanged} files, ${result.linesChanged} lines`,
      );
    },
  );

program
  .command("status")
  .description("Show candidates and rate-limit usage")
  .option("--status <s>", "Filter by status")
  .action(async (opts: { status?: string }) => {
    await initOoCoder();
    const policy = loadPolicy();
    const allowlist = loadAllowlist();
    console.log(
      `Allowlist: ${allowlist.repos.length} repos, ${allowlist.orgs.length} orgs (strict=${allowlist.strict})`,
    );
    console.log(
      `Limits/day: issues=${policy.rateLimits.maxIssuesPerDay} prs=${policy.rateLimits.maxPrsPerDay} comments=${policy.rateLimits.maxCommentsPerDay}`,
    );
    const rows = listCandidates(
      opts.status as
        | "discovered"
        | "discussed"
        | "working"
        | "pr_opened"
        | "failed"
        | "skipped"
        | undefined,
    );
    if (rows.length === 0) {
      console.log("No candidates in DB. Run: oocoder discover");
      return;
    }
    for (const row of rows) {
      console.log(
        `${row.status.padEnd(10)} ${row.id}  score=${row.score}  ${row.title}`,
      );
      if (row.prUrl) console.log(`           PR ${row.prUrl}`);
    }
  });

program
  .command("daemon")
  .description(
    "Autonomous loop: discover → discuss → work on top N allowlisted issues",
  )
  .option("-n, --limit <n>", "Max issues to process this run", "1")
  .option("--dry-run", "Do not post comments or open PRs", false)
  .option("--runtime <mode>", "local | cloud", "local")
  .option("--skip-discuss", "Skip discussion comments", false)
  .action(
    async (opts: {
      limit: string;
      dryRun?: boolean;
      runtime: string;
      skipDiscuss?: boolean;
    }) => {
      await initOoCoder();
      const runtime = opts.runtime as RuntimeMode;
      const limit = Number(opts.limit);
      const issues = await discoverIssues({ limit });
      const allowlist = loadAllowlist();
      const actionable = issues.filter((i) => {
        if (allowlist.repos.length === 0 && allowlist.orgs.length === 0) {
          return false;
        }
        return true;
      });

      if (actionable.length === 0) {
        console.log(
          "Nothing to process. Populate allowlist and ensure discover returns matches.",
        );
        return;
      }

      let processed = 0;
      for (const issue of actionable) {
        if (processed >= limit) break;
        console.log(`\n=== Working ${issue.owner}/${issue.repo}#${issue.number} ===`);
        try {
          const result = await workOnIssue({
            issueUrl: issue.url,
            dryRun: opts.dryRun,
            skipDiscuss: opts.skipDiscuss,
            runtime,
          });
          console.log(result.message);
          processed += 1;
        } catch (err) {
          console.error(
            `Failed: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
      console.log(`\nDaemon finished. Processed ${processed} issue(s).`);
      const auth = await checkAuth();
      if (auth.ghLogin && loadPolicy().ciFix.enabled) {
        const fixes = await followUpCiFixes({
          login: auth.ghLogin,
          dryRun: opts.dryRun,
        });
        for (const fix of fixes) {
          console.log(`CI ${fix.outcome ?? fix.reason ?? "skip"} ${fix.pr ?? ""}`.trim());
        }
      }
    },
  );

program
  .command("fixci")
  .description("Repair CI on pull requests OoCoder opened")
  .option("--pr <ref>", "One pull URL or owner/repo#number")
  .option("--dry-run", "Diagnose and draft only; do not push or comment", false)
  .action(async (opts: { pr?: string; dryRun?: boolean }) => {
    await initOoCoder();
    const auth = await checkAuth();
    if (!auth.ghLogin) {
      throw new Error(auth.message);
    }
    const results = await followUpCiFixes({
      login: auth.ghLogin,
      dryRun: opts.dryRun,
      pr: opts.pr,
    });
    if (results.length === 0) {
      console.log("No open OoCoder pull requests to repair.");
      return;
    }
    for (const result of results) {
      console.log(
        [
          result.ok ? "ok" : "skip",
          result.outcome ?? result.reason ?? "",
          result.branch ?? "",
          result.pr ?? "",
          result.standDown ? "stand-down drafted" : "",
        ]
          .filter(Boolean)
          .join(" "),
      );
    }
  });

program
  .command("dashboard")
  .description("Write an offline stats.html from the local OoCoder database")
  .option("-o, --out <file>", "Output HTML path", "stats.html")
  .option("--days <n>", "History window in days")
  .option("--no-refresh", "Skip live pull-state refresh")
  .action(async (opts: { out: string; days?: string; refresh?: boolean }) => {
    await initOoCoder();
    const written = await writeDashboard({
      out: opts.out,
      days: opts.days ? Number(opts.days) : undefined,
      refresh: opts.refresh,
    });
    console.log(
      `Wrote ${written.out} (${written.model.kpis.actionsTotal} actions, ${written.model.kpis.prsOpened} PRs)`,
    );
  });

async function main(): Promise<void> {
  try {
    await program.parseAsync(process.argv);
  } finally {
    closeDb();
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  closeDb();
  process.exit(1);
});
