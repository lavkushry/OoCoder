import { loadPolicy } from "./config.js";
import {
  blastRadiusOk,
  gateCiCandidate,
  type CheckRunSnapshot,
  type CombinedStatusSnapshot,
  type PullSnapshot,
} from "./ciGate.js";
import { dedupeDiagnostics, parseErrors, type Diagnostic } from "./ciParse.js";
import {
  actionsRunsForSha,
  checkRunAnnotations,
  checkRuns,
  combinedStatus,
  getPull,
  jobLogs,
  parsePullUrl,
  postIssueComment,
} from "./github.js";
import { buildRepairPrompt, implementIssue } from "./implement.js";
import { oocoderHome } from "./paths.js";
import { diffStats, ensureRepairWorktree, pushBranch } from "./git.js";
import { runBestEffortVerify } from "./quality.js";
import { runOk } from "./shell.js";
import {
  ciAttemptCount,
  ciAttemptedSha,
  ciHasOutcome,
  listCandidates,
  logEvent,
  recordCiAttempt,
  type CiOutcomeName,
} from "./state.js";
import type { CiFixConfig } from "./types.js";

export interface CiApi {
  getPull(owner: string, repo: string, number: number): Promise<Record<string, unknown>>;
  checkRuns(owner: string, repo: string, sha: string): Promise<Array<Record<string, unknown>>>;
  combinedStatus(
    owner: string,
    repo: string,
    sha: string,
  ): Promise<CombinedStatusSnapshot>;
  annotations(
    owner: string,
    repo: string,
    checkRunId: number,
  ): Promise<Array<Record<string, unknown>>>;
  actionsRuns(owner: string, repo: string, sha: string): Promise<Array<Record<string, unknown>>>;
  jobLogs(owner: string, repo: string, jobId: number, maxBytes: number): Promise<string>;
  comment(owner: string, repo: string, number: number, body: string): Promise<string>;
}

export const liveCiApi: CiApi = {
  getPull,
  checkRuns,
  combinedStatus: async (owner, repo, sha) => {
    const status = await combinedStatus(owner, repo, sha);
    return { state: status.state };
  },
  annotations: checkRunAnnotations,
  actionsRuns: actionsRunsForSha,
  jobLogs,
  comment: postIssueComment,
};

export interface RepairResult {
  changedFiles: string[];
  testsPassed: boolean;
  ok: boolean;
  summary: string;
}

export interface CiFixResult {
  ok: boolean;
  reason?: string;
  outcome?: CiOutcomeName;
  branch?: string;
  pushed?: boolean;
  standDown?: string | null;
  pr?: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function snapshotFromPull(data: Record<string, unknown>): PullSnapshot {
  const user = asRecord(data.user);
  const head = asRecord(data.head);
  const headRepo = asRecord(head.repo);
  const headOwner = asRecord(headRepo.owner);
  const base = asRecord(data.base);
  const pushed =
    (typeof headRepo.pushed_at === "string" && headRepo.pushed_at) ||
    (typeof data.updated_at === "string" && data.updated_at) ||
    new Date(0).toISOString();
  return {
    number: Number(data.number),
    state: String(data.state ?? ""),
    draft: Boolean(data.draft),
    merged: Boolean(data.merged),
    title: String(data.title ?? ""),
    htmlUrl: String(data.html_url ?? ""),
    updatedAt: String(data.updated_at ?? ""),
    userLogin: String(user.login ?? ""),
    headRef: String(head.ref ?? ""),
    headSha: String(head.sha ?? ""),
    headOwner: String(headOwner.login ?? ""),
    baseRef: String(base.ref ?? "main"),
    pushedAt: pushed,
  };
}

function runsFromApi(rows: Array<Record<string, unknown>>): CheckRunSnapshot[] {
  return rows.map((row) => ({
    id: Number(row.id),
    name: String(row.name ?? "check"),
    status: String(row.status ?? ""),
    conclusion: row.conclusion == null ? null : String(row.conclusion),
  }));
}

export function standDownBody(input: {
  attempts: number;
  detail: string;
  disclosure: string;
}): string {
  return [
    "I tried to repair CI on this pull request and I am stopping so I do not keep pushing noise.",
    "",
    `Attempts used: ${input.attempts}.`,
    input.detail ? `Last result: ${input.detail}` : "",
    "",
    "If a maintainer can point at the right fix, I can try again on a fresh commit.",
    "",
    input.disclosure.trim(),
  ]
    .filter((line) => line !== "")
    .join("\n");
}

export async function collectDiagnostics(
  api: CiApi,
  owner: string,
  repo: string,
  sha: string,
  failed: CheckRunSnapshot[],
  maxBytes: number,
): Promise<Diagnostic[]> {
  const found: Diagnostic[] = [];
  for (const run of failed.slice(0, 5)) {
    try {
      const notes = await api.annotations(owner, repo, run.id);
      for (const note of notes.slice(0, 10)) {
        found.push({
          kind: "compile",
          file: note.path == null ? null : String(note.path),
          line: note.start_line == null ? null : Number(note.start_line),
          message: String(note.message ?? "").slice(0, 500),
          source: "annotation",
        });
      }
    } catch {
      // annotations are optional
    }
  }
  if (found.length >= 5) return dedupeDiagnostics(found);

  const runs = await api.actionsRuns(owner, repo, sha);
  for (const workflow of runs.slice(0, 3)) {
    const jobs = Array.isArray(workflow.jobs)
      ? (workflow.jobs as Array<Record<string, unknown>>)
      : [];
    for (const job of jobs.slice(0, 2)) {
      const id = Number(job.id);
      if (!Number.isFinite(id)) continue;
      const log = await api.jobLogs(owner, repo, id, maxBytes);
      found.push(...parseErrors(log, "job-log"));
    }
  }
  return dedupeDiagnostics(found);
}

export async function runCiFix(options: {
  owner: string;
  repo: string;
  number: number;
  login: string;
  dryRun?: boolean;
  home?: string;
  cfg?: CiFixConfig;
  api?: CiApi;
  nowMs?: number;
  fixesThisRun?: number;
  cwd?: string;
  repair?: (input: {
    cwd: string;
    prompt: string;
    branch: string;
  }) => Promise<RepairResult>;
  push?: (cwd: string, branch: string) => Promise<void>;
  disclosure?: string;
}): Promise<CiFixResult> {
  const home = options.home ?? oocoderHome();
  const policy = loadPolicy(home);
  const cfg = options.cfg ?? policy.ciFix;
  const api = options.api ?? liveCiApi;
  const nowMs = options.nowMs ?? Date.now();
  const repoFull = `${options.owner}/${options.repo}`;
  const raw = await api.getPull(options.owner, options.repo, options.number);
  const pr = snapshotFromPull(raw);
  const attemptCount = ciAttemptCount(repoFull, pr.number, home);
  const alreadySha = ciAttemptedSha(repoFull, pr.number, pr.headSha, home);
  const stoodDown = ciHasOutcome(repoFull, pr.number, "gave_up", home);

  if (attemptCount >= cfg.maxAttemptsPerPr && !stoodDown) {
    const body = standDownBody({
      attempts: attemptCount,
      detail: "attempt cap reached",
      disclosure: options.disclosure ?? policy.disclosure,
    });
    if (options.dryRun) {
      return {
        ok: false,
        reason: "attempt_cap",
        outcome: "gave_up",
        standDown: body,
        pushed: false,
        pr: pr.htmlUrl,
      };
    }
    await api.comment(options.owner, options.repo, pr.number, body);
    recordCiAttempt(repoFull, pr.number, pr.headSha, "gave_up", "attempt cap", home);
    logEvent("lesson", repoFull, { pr: pr.number, lesson: body }, home);
    return { ok: false, reason: "attempt_cap", outcome: "gave_up", standDown: body };
  }

  const runs = runsFromApi(await api.checkRuns(options.owner, options.repo, pr.headSha));
  const combined = await api.combinedStatus(options.owner, options.repo, pr.headSha);
  const gate = gateCiCandidate({
    cfg,
    pr,
    login: options.login,
    runs,
    combined,
    nowMs,
    alreadySha,
    attemptCount,
    fixesThisRun: options.fixesThisRun ?? 0,
    alreadyStoodDown: stoodDown,
  });
  if (!gate.ok) {
    return { ok: false, reason: gate.reason, pr: pr.htmlUrl };
  }

  const diagnostics = await collectDiagnostics(
    api,
    options.owner,
    options.repo,
    pr.headSha,
    runs.filter((run) => run.conclusion === "failure" || run.conclusion === "timed_out"),
    cfg.maxLogBytes,
  );
  const diagText = diagnostics
    .map(
      (item) =>
        `[${item.source}] ${item.file ?? "?"}${item.line ? `:${item.line}` : ""} — ${item.message}`,
    )
    .join("\n");

  if (options.dryRun) {
    recordCiAttempt(
      repoFull,
      pr.number,
      pr.headSha,
      "dry_run",
      diagText.slice(0, 1000),
      home,
    );
    return {
      ok: true,
      outcome: "dry_run",
      reason: `dry-run would repair ${gate.failedNames.join(", ")}`,
      branch: pr.headRef,
      pushed: false,
      pr: pr.htmlUrl,
    };
  }

  const prompt = buildRepairPrompt({
    title: pr.title,
    number: pr.number,
    branch: pr.headRef,
    sha: pr.headSha,
    failedChecks: gate.failedNames,
    diagnostics: diagText,
  });

  let cwd: string;
  try {
    cwd = await ensureRepairWorktree({
      owner: options.owner,
      repo: options.repo,
      number: pr.number,
      branch: pr.headRef,
      headOwner: pr.headOwner || options.login,
      home,
      explicitCwd: options.cwd,
    });
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    logEvent(
      "ci_fix_no_worktree",
      pr.htmlUrl,
      { reason },
      home,
    );
    return {
      ok: false,
      reason: "no_worktree",
      branch: pr.headRef,
      pr: pr.htmlUrl,
    };
  }

  const repair =
    options.repair ??
    (async () => {
      const impl = await implementIssue({
        issue: {
          owner: options.owner,
          repo: options.repo,
          number: pr.number,
          url: pr.htmlUrl,
          title: pr.title,
          body: diagText,
          labels: [],
          comments: 0,
          createdAt: pr.updatedAt,
          updatedAt: pr.updatedAt,
          score: 0,
        },
        cwd,
        runtime: "local",
        promptOverride: prompt,
      });
      const stats = await diffStats(cwd, "HEAD");
      const names = stats.filesChanged
        ? await runOk("git", ["diff", "--name-only", "HEAD"], { cwd })
        : "";
      const verify = await runBestEffortVerify(cwd);
      return {
        changedFiles: names
          ? names.split("\n").map((line) => line.trim()).filter(Boolean)
          : [],
        testsPassed: !verify.ran || verify.ok,
        ok: impl.status === "finished",
        summary: impl.resultText ?? "",
      };
    });

  const repaired = await repair({ cwd, prompt, branch: pr.headRef });
  const radius = blastRadiusOk(
    repaired.changedFiles,
    diagnostics.map((item) => item.file),
    cfg.maxFilesTouched,
  );
  if (!radius.ok || !repaired.testsPassed) {
    const outcome: CiOutcomeName = repaired.testsPassed ? "no_fix" : "tests_failed";
    recordCiAttempt(
      repoFull,
      pr.number,
      pr.headSha,
      outcome,
      radius.ok ? "tests failed" : radius.reason,
      home,
    );
    const nextCount = ciAttemptCount(repoFull, pr.number, home);
    if (nextCount >= cfg.maxAttemptsPerPr) {
      const body = standDownBody({
        attempts: nextCount,
        detail: radius.ok ? "tests failed" : radius.reason,
        disclosure: options.disclosure ?? policy.disclosure,
      });
      await api.comment(options.owner, options.repo, pr.number, body);
      recordCiAttempt(repoFull, pr.number, pr.headSha, "gave_up", body.slice(0, 400), home);
      logEvent("lesson", repoFull, { pr: pr.number, lesson: body }, home);
      return { ok: false, outcome: "gave_up", reason: outcome, standDown: body, branch: pr.headRef };
    }
    return { ok: false, outcome, reason: radius.ok ? "tests_failed" : radius.reason, branch: pr.headRef };
  }

  const push = options.push ?? pushBranch;
  await push(cwd, pr.headRef);
  recordCiAttempt(repoFull, pr.number, pr.headSha, "patched", repaired.summary.slice(0, 400), home);
  logEvent("ci_fix", pr.htmlUrl, { branch: pr.headRef, files: repaired.changedFiles }, home);
  return {
    ok: true,
    outcome: "patched",
    branch: pr.headRef,
    pushed: true,
    pr: pr.htmlUrl,
  };
}

export async function followUpCiFixes(options: {
  login: string;
  dryRun?: boolean;
  home?: string;
  pr?: string;
  api?: CiApi;
  nowMs?: number;
  repair?: (input: { cwd: string; prompt: string; branch: string }) => Promise<RepairResult>;
  push?: (cwd: string, branch: string) => Promise<void>;
  cwd?: string;
}): Promise<CiFixResult[]> {
  const home = options.home ?? oocoderHome();
  const policy = loadPolicy(home);
  const results: CiFixResult[] = [];
  let fixes = 0;

  const targets = options.pr
    ? [options.pr]
    : listCandidates("pr_opened", home)
        .map((row) => row.prUrl)
        .filter((url): url is string => Boolean(url));

  for (const target of targets) {
    if (!options.dryRun && fixes >= policy.ciFix.maxFixesPerRun) break;
    let parsed: { owner: string; repo: string; number: number };
    try {
      parsed = parsePullUrl(target);
    } catch {
      continue;
    }
    const result = await runCiFix({
      ...parsed,
      login: options.login,
      dryRun: options.dryRun,
      home,
      api: options.api,
      nowMs: options.nowMs,
      fixesThisRun: fixes,
      repair: options.repair,
      push: options.push,
      cwd: options.cwd,
    });
    results.push(result);
    if (result.outcome === "patched") fixes += 1;
  }
  return results;
}
