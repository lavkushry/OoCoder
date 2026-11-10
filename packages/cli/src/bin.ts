#!/usr/bin/env node
import { Command } from "commander";
import {
  checkAuth,
  closeDb,
  discoverIssues,
  discussIssue,
  followUpCiFixes,
  formatCampaignLearningResult,
  formatCampaignMonitor,
  formatCampaignStatus,
  formatCampaignWatchTick,
  getCampaignStatus,
  initOoCoder,
  isAllowed,
  listCandidates,
  loadAllowlist,
  loadPolicy,
  logCampaignLearning,
  monitorCampaignPrs,
  runCampaign,
  runCampaignWatch,
  scrubCampaignBranding,
  syncCampaignPrs,
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
  .option("--skip-verify", "Skip compile/test verify gate", false)
  .option("--runtime <mode>", "local | cloud", "local")
  .option("--model <id>", "Cursor model id", "composer-2.5")
  .action(
    async (
      issue: string,
      opts: {
        dryRun?: boolean;
        skipPr?: boolean;
        skipDiscuss?: boolean;
        skipVerify?: boolean;
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
        skipVerify: opts.skipVerify,
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
  .option("--discuss", "Post discussion comments before work", false)
  .option("--skip-verify", "Skip compile/test verify gate", false)
  .option("--skip-pr", "Commit locally but do not open a PR", false)
  .action(
    async (opts: {
      limit: string;
      dryRun?: boolean;
      runtime: string;
      discuss?: boolean;
      skipVerify?: boolean;
      skipPr?: boolean;
    }) => {
      await initOoCoder();
      const runtime = opts.runtime as RuntimeMode;
      const limit = Number(opts.limit);
      const issues = await discoverIssues({ limit: Math.max(limit * 5, 40) });
      const allowlist = loadAllowlist();
      if (allowlist.repos.length === 0 && allowlist.orgs.length === 0) {
        console.log(
          "Nothing to process. Populate allowlist and ensure discover returns matches.",
        );
        return;
      }

      const actionable: typeof issues = [];
      for (const issue of issues) {
        if (!isAllowed(issue.owner, issue.repo, allowlist)) {
          console.log(
            `Skipping ${issue.owner}/${issue.repo}#${issue.number} (not allowlisted)`,
          );
          continue;
        }
        actionable.push(issue);
      }

      if (actionable.length === 0) {
        console.log(
          "Nothing to process. Populate allowlist and ensure discover returns matches.",
        );
        return;
      }

      const already = new Set(
        listCandidates()
          .filter((c) => c.status === "pr_opened" || c.status === "working")
          .map((c) => c.id),
      );

      let processed = 0;
      for (const issue of actionable) {
        if (processed >= limit) break;
        const id = `${issue.owner}/${issue.repo}#${issue.number}`;
        if (already.has(id)) {
          console.log(`Skipping ${id} (already ${"pr_opened/working"})`);
          continue;
        }
        console.log(`\n=== Working ${id} ===`);
        try {
          const result = await workOnIssue({
            issueUrl: issue.url,
            dryRun: opts.dryRun,
            skipDiscuss: !opts.discuss,
            skipVerify: opts.skipVerify,
            skipPr: opts.skipPr,
            runtime,
          });
          console.log(result.message);
          if (result.prUrl) console.log(`PR: ${result.prUrl}`);
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

const campaign = program
  .command("campaign")
  .description("Manage the DE contribution campaign (status, sync, scrub, monitor, run)");

campaign
  .command("status")
  .description("Show progress toward the campaign PR target")
  .action(async () => {
    await initOoCoder();
    console.log(formatCampaignStatus(getCampaignStatus()));
  });

campaign
  .command("sync")
  .description("Import allowlisted open PRs you authored into the campaign DB")
  .action(async () => {
    await initOoCoder();
    const result = await syncCampaignPrs();
    console.log(`Synced ${result.synced} allowlisted open PR(s).`);
    console.log(formatCampaignStatus(getCampaignStatus()));
  });

campaign
  .command("scrub")
  .description("Strip Cursor/OoCoder footers from allowlisted open PR bodies")
  .action(async () => {
    await initOoCoder();
    const result = await scrubCampaignBranding();
    console.log(`Scrubbed ${result.scrubbed}/${result.checked} open PR(s).`);
  });

campaign
  .command("monitor")
  .description(
    "Scan open allowlisted PRs for maintainer feedback, CHANGES_REQUESTED, or failing CI",
  )
  .action(async () => {
    await initOoCoder();
    const result = await monitorCampaignPrs();
    console.log(formatCampaignMonitor(result));
    console.log(formatCampaignStatus(getCampaignStatus()));
  });

campaign
  .command("learn")
  .description(
    "Append a new campaign learning to docs/CAMPAIGN_RULE_LOG.md (and audit DB)",
  )
  .requiredOption("--repo <owner/name>", "Repo or topic slug (e.g. redis/redis)")
  .requiredOption("--title <text>", "Short learning title")
  .requiredOption("--rule <text>", "Durable rule text to remember")
  .option("--source <ref>", "Provenance (PR #, CI check, reviewer)")
  .option("--date <YYYY-MM-DD>", "Log date (default: today UTC)")
  .option(
    "--encode",
    "Mark that CAMPAIGN_LEARNINGS.md + implement.ts should be updated",
    false,
  )
  .action(
    async (opts: {
      repo: string;
      title: string;
      rule: string;
      source?: string;
      date?: string;
      encode?: boolean;
    }) => {
      await initOoCoder();
      const result = logCampaignLearning({
        repo: opts.repo,
        title: opts.title,
        rule: opts.rule,
        source: opts.source,
        date: opts.date,
        encode: opts.encode,
      });
      console.log(formatCampaignLearningResult(result));
    },
  );

campaign
  .command("watch")
  .description(
    "Continuous 24x7 loop: sync → scrub → monitor → optional open PRs → fixci",
  )
  .option(
    "--interval <sec>",
    "Seconds between ticks (default 300)",
    "300",
  )
  .option(
    "--max-ticks <n>",
    "Stop after N ticks (0 = forever; use 1 for systemd oneshot)",
    "0",
  )
  .option("--open-prs", "Also open new PRs toward the campaign target each tick", false)
  .option("-n, --limit <n>", "Max new PRs per tick when --open-prs is set")
  .option("--dry-run", "Do not push, comment, or open PRs", false)
  .option("--skip-verify", "Skip compile/test verify when opening PRs", false)
  .option("--runtime <mode>", "local | cloud", "local")
  .option("--model <id>", "Cursor model id", "composer-2.5")
  .action(
    async (opts: {
      interval: string;
      maxTicks: string;
      openPrs?: boolean;
      limit?: string;
      dryRun?: boolean;
      skipVerify?: boolean;
      runtime: string;
      model: string;
    }) => {
      await initOoCoder();
      const runtime = opts.runtime as RuntimeMode;
      if (runtime !== "local" && runtime !== "cloud") {
        throw new Error(`Invalid --runtime: ${opts.runtime}`);
      }
      const intervalSec = Number(opts.interval);
      const maxTicks = Number(opts.maxTicks);
      console.log(
        `Campaign watch starting (interval=${intervalSec}s maxTicks=${maxTicks || "∞"} openPrs=${Boolean(opts.openPrs)})`,
      );
      await runCampaignWatch({
        intervalSec,
        maxTicks,
        openPrs: opts.openPrs,
        openLimit: opts.limit ? Number(opts.limit) : undefined,
        dryRun: opts.dryRun,
        skipVerify: opts.skipVerify,
        runtime,
        model: opts.model,
        onTick: async (tick) => {
          console.log(formatCampaignWatchTick(tick));
        },
      });
    },
  );

campaign
  .command("run")
  .description(
    "Discover and work issues until the campaign/daily PR target for this batch",
  )
  .option("-n, --limit <n>", "Max PRs to open this run (default: policy campaign.batchSize)")
  .option("--dry-run", "Analyze without posting or opening PRs", false)
  .option("--discuss", "Post discussion comments before work", false)
  .option("--verify", "Run compile/test verify gate", false)
  .option("--skip-pr", "Commit locally but do not open a PR", false)
  .option("--runtime <mode>", "local | cloud", "local")
  .option("--model <id>", "Cursor model id", "composer-2.5")
  .action(
    async (opts: {
      limit?: string;
      dryRun?: boolean;
      discuss?: boolean;
      verify?: boolean;
      skipPr?: boolean;
      runtime: string;
      model: string;
    }) => {
      await initOoCoder();
      const runtime = opts.runtime as RuntimeMode;
      if (runtime !== "local" && runtime !== "cloud") {
        throw new Error(`Invalid --runtime: ${opts.runtime}`);
      }
      const result = await runCampaign({
        dryRun: opts.dryRun,
        skipDiscuss: !opts.discuss,
        skipVerify: !opts.verify,
        skipPr: opts.skipPr,
        limit: opts.limit ? Number(opts.limit) : undefined,
        runtime,
        model: opts.model,
      });
      for (const line of result.messages) console.log(line);
      console.log(
        `\nCampaign batch done. processed=${result.processed} opened=${result.opened} failed=${result.failed}`,
      );
      console.log(formatCampaignStatus(result.status));
    },
  );

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
