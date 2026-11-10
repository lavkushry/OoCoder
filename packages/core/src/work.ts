import { isAllowed, loadAllowlist, loadPolicy } from "./config.js";
import { discussIssue } from "./discuss.js";
import {
  checkAuth,
  createPullRequest,
  defaultBranch,
  ensureFork,
  fetchIssue,
} from "./github.js";
import {
  commitAll,
  createWorktree,
  ensureMirrorClone,
  pushBranch,
} from "./git.js";
import { implementIssue } from "./implement.js";
import { checkDiffQuality, runBestEffortVerify } from "./quality.js";
import { runOk } from "./shell.js";
import {
  assertRateLimit,
  bumpRate,
  logEvent,
  updateCandidate,
  upsertCandidate,
} from "./state.js";
import { oocoderHome } from "./paths.js";
import type { WorkOptions, WorkResult } from "./types.js";

export async function workOnIssue(
  options: WorkOptions,
  home = oocoderHome(),
): Promise<WorkResult> {
  const policy = loadPolicy(home);
  const allowlist = loadAllowlist(home);
  const runtime = options.runtime ?? "local";
  const dryRun = Boolean(options.dryRun);
  const skipPr = Boolean(options.skipPr);

  const auth = await checkAuth();
  if (!auth.git || !auth.gh) {
    throw new Error(`Auth/prereq failure: ${auth.message}`);
  }
  if (!dryRun && runtime === "local" && !auth.cursorApiKey && !auth.grokApiKey) {
    throw new Error(auth.message);
  }
  if (!auth.ghLogin && !dryRun) {
    throw new Error("Could not resolve GitHub login via gh api user");
  }

  const issue = await fetchIssue(options.issueUrl);
  issue.score = 0;
  upsertCandidate(issue, home);

  if (!dryRun && !isAllowed(issue.owner, issue.repo, allowlist)) {
    updateCandidate(`${issue.owner}/${issue.repo}#${issue.number}`, {
      status: "skipped",
      notes: "Not allowlisted",
    }, home);
    throw new Error(
      `${issue.owner}/${issue.repo} is not on the allowlist. Edit ~/.oocoder/allowlist.yaml`,
    );
  }

  if (!dryRun) {
    assertRateLimit("issues", policy.rateLimits.maxIssuesPerDay, home);
    if (!skipPr) {
      assertRateLimit("prs", policy.rateLimits.maxPrsPerDay, home);
    }
  }

  if (!options.skipDiscuss && !dryRun) {
    try {
      await discussIssue(issue.url, { dryRun: false }, home);
    } catch (err) {
      // Discussion is best-effort; continue working.
      logEvent(
        "discuss_error",
        issue.url,
        { error: err instanceof Error ? err.message : String(err) },
        home,
      );
    }
  }

  updateCandidate(`${issue.owner}/${issue.repo}#${issue.number}`, {
    status: "working",
  }, home);
  bumpRate("issues", home);

  // Cloud path: Cursor opens the PR.
  if (runtime === "cloud") {
    if (dryRun) {
      return {
        issue,
        branch: `oocoder/issue-${issue.number}-dry-run`,
        worktreePath: null,
        prUrl: null,
        dryRun: true,
        skippedPr: skipPr,
        filesChanged: 0,
        linesChanged: 0,
        agentId: null,
        message: "Dry run (cloud) — no agent started",
      };
    }

    const impl = await implementIssue({
      issue,
      cwd: process.cwd(),
      runtime: "cloud",
      model: options.model,
      dryRun: false,
    });

    if (impl.prUrl) {
      bumpRate("prs", home);
      updateCandidate(`${issue.owner}/${issue.repo}#${issue.number}`, {
        status: "pr_opened",
        prUrl: impl.prUrl,
        notes: `cloud agent ${impl.agentId}`,
      }, home);
    }

    logEvent("work_cloud", issue.url, impl, home);

    return {
      issue,
      branch: "cloud",
      worktreePath: null,
      prUrl: impl.prUrl,
      dryRun: false,
      skippedPr: false,
      filesChanged: 0,
      linesChanged: 0,
      agentId: impl.agentId,
      message: impl.resultText ?? `Cloud agent finished with status ${impl.status}`,
    };
  }

  // Local path: fork → worktree → Cursor local → gates → gh pr.
  if (dryRun) {
    const branch = `oocoder/issue-${issue.number}-dry-run`;
    logEvent(
      "work_local_dry_run",
      issue.url,
      { branch, skipPr, runtime },
      home,
    );
    return {
      issue,
      branch,
      worktreePath: null,
      prUrl: null,
      dryRun: true,
      skippedPr: skipPr,
      filesChanged: 0,
      linesChanged: 0,
      agentId: null,
      message:
        "Dry run — issue fetched and policy checked; fork/worktree/Cursor skipped",
    };
  }

  const fork = await ensureFork(issue.owner, issue.repo, auth.ghLogin ?? "unknown");
  const base = await defaultBranch(issue.owner, issue.repo);
  const mirrorDir = await ensureMirrorClone({
    cloneUrl: fork.cloneUrl,
    owner: fork.forkOwner,
    repo: issue.repo,
    upstream: fork.upstream,
    home,
  });

  const { worktreePath, branch } = await createWorktree({
    mirrorDir,
    owner: issue.owner,
    repo: issue.repo,
    issueNumber: issue.number,
    title: issue.title,
    baseBranch: base,
    home,
  });

  // Capture pre-edit HEAD for quality gates.
  const preHead = await runOk("git", ["rev-parse", "HEAD"], {
    cwd: worktreePath,
  });

  const impl = await implementIssue({
    issue,
    cwd: worktreePath,
    runtime: "local",
    model: options.model,
    dryRun: false,
  });

  const quality = await checkDiffQuality(worktreePath, preHead, policy);
  if (!quality.ok) {
    updateCandidate(`${issue.owner}/${issue.repo}#${issue.number}`, {
      status: "failed",
      notes: quality.reason ?? "quality gate failed",
    }, home);
    throw new Error(quality.reason ?? "Quality gate failed");
  }

  const verify = await runBestEffortVerify(worktreePath);
  if (verify.ran && !verify.ok) {
    logEvent(
      "verify_failed",
      issue.url,
      { command: verify.command, output: verify.output.slice(0, 4000) },
      home,
    );
    // Soft fail: still allow PR but note it — autonomous mode prefers shipping with disclosure.
  }

  const committed = await commitAll(
    worktreePath,
    `fix: ${issue.title}\n\nResolves #${issue.number}\n\nCo-authored-by: OoCoder <oocoder@users.noreply.github.com>`,
  );

  if (!committed) {
    updateCandidate(`${issue.owner}/${issue.repo}#${issue.number}`, {
      status: "failed",
      notes: "No changes produced by agent",
    }, home);
    throw new Error("Agent produced no file changes");
  }

  let prUrl: string | null = null;
  if (!skipPr) {
    await pushBranch(worktreePath, branch);
    const head =
      fork.forkOwner === issue.owner
        ? branch
        : `${fork.forkOwner}:${branch}`;
    const body = [
      `## Summary`,
      `Addresses #${issue.number}: ${issue.title}`,
      "",
      impl.resultText ? `### Agent notes\n${impl.resultText}` : "",
      verify.ran
        ? `### Verification\n\`${verify.command}\` → ${verify.ok ? "pass" : "fail (see logs)"}`
        : "### Verification\nNo project test command detected.",
      "",
      policy.prFooter.trim(),
    ]
      .filter(Boolean)
      .join("\n");

    prUrl = await createPullRequest({
      cwd: worktreePath,
      title: `Fix: ${issue.title}`,
      body,
      head,
      base,
      repo: `${issue.owner}/${issue.repo}`,
    });
    bumpRate("prs", home);
    updateCandidate(`${issue.owner}/${issue.repo}#${issue.number}`, {
      status: "pr_opened",
      prUrl,
      notes: `local agent ${impl.agentId}; files=${quality.filesChanged} lines=${quality.linesChanged}`,
    }, home);
  } else {
    updateCandidate(`${issue.owner}/${issue.repo}#${issue.number}`, {
      status: "working",
      notes: "Changes committed locally; --skip-pr",
    }, home);
  }

  logEvent(
    "work_local",
    issue.url,
    {
      branch,
      worktreePath,
      prUrl,
      quality,
      agentId: impl.agentId,
      preHead,
    },
    home,
  );

  return {
    issue,
    branch,
    worktreePath,
    prUrl,
    dryRun: false,
    skippedPr: skipPr,
    filesChanged: quality.filesChanged,
    linesChanged: quality.linesChanged,
    agentId: impl.agentId,
    message: prUrl
      ? `Opened PR ${prUrl}`
      : skipPr
        ? `Committed on ${branch} (PR skipped)`
        : "Work finished",
  };
}
