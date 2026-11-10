import type { CiFixConfig } from "./types.js";

export interface PullSnapshot {
  number: number;
  state: string;
  draft: boolean;
  merged: boolean;
  title: string;
  htmlUrl: string;
  updatedAt: string;
  userLogin: string;
  headRef: string;
  headSha: string;
  headOwner: string;
  baseRef: string;
  /** ISO timestamp of the head repo push, or PR updated_at. */
  pushedAt: string;
}

export interface CheckRunSnapshot {
  id: number;
  name: string;
  status: string;
  conclusion: string | null;
}

export interface CombinedStatusSnapshot {
  state: string;
}

export type GateReason =
  | "disabled"
  | "not_open"
  | "draft"
  | "merged"
  | "not_ours"
  | "bad_branch"
  | "pending"
  | "not_failure"
  | "quiet_period"
  | "sha_dedup"
  | "attempt_cap"
  | "run_budget"
  | "already_stood_down";

const FIXABLE = new Set(["failure", "timed_out"]);
const IGNORE = new Set(["cancelled", "skipped", "neutral", "action_required", "success"]);

export function isFixableConclusion(conclusion: string | null): boolean {
  if (!conclusion) return false;
  return FIXABLE.has(conclusion);
}

export function classifyChecks(runs: CheckRunSnapshot[]): {
  pending: boolean;
  failedNames: string[];
  failedRuns: CheckRunSnapshot[];
} {
  const pending = runs.some((run) => run.status !== "completed");
  const failedRuns = runs.filter(
    (run) => run.status === "completed" && isFixableConclusion(run.conclusion),
  );
  return {
    pending,
    failedNames: failedRuns.map((run) => run.name),
    failedRuns,
  };
}

export function legacyFailure(status: CombinedStatusSnapshot | null): boolean {
  if (!status) return false;
  return status.state === "failure" || status.state === "error";
}

export function legacyPending(status: CombinedStatusSnapshot | null): boolean {
  return status?.state === "pending";
}

export function quietElapsed(
  pushedAtIso: string,
  nowMs: number,
  minMinutes: number,
): boolean {
  const pushed = Date.parse(pushedAtIso);
  if (Number.isNaN(pushed)) return false;
  return nowMs - pushed >= minMinutes * 60 * 1000;
}

export function gateCiCandidate(input: {
  cfg: CiFixConfig;
  pr: PullSnapshot;
  login: string;
  runs: CheckRunSnapshot[];
  combined: CombinedStatusSnapshot | null;
  nowMs: number;
  alreadySha: boolean;
  attemptCount: number;
  fixesThisRun: number;
  alreadyStoodDown: boolean;
}): { ok: true; failedNames: string[] } | { ok: false; reason: GateReason } {
  if (!input.cfg.enabled) return { ok: false, reason: "disabled" };
  if (input.pr.state !== "open") return { ok: false, reason: "not_open" };
  if (input.pr.draft) return { ok: false, reason: "draft" };
  if (input.pr.merged) return { ok: false, reason: "merged" };
  if (input.pr.userLogin !== input.login) return { ok: false, reason: "not_ours" };
  if (!input.pr.headRef.startsWith("oocoder/")) {
    return { ok: false, reason: "bad_branch" };
  }
  if (input.pr.headOwner !== input.login) return { ok: false, reason: "bad_branch" };
  if (input.alreadyStoodDown) return { ok: false, reason: "already_stood_down" };
  if (input.attemptCount >= input.cfg.maxAttemptsPerPr) {
    return { ok: false, reason: "attempt_cap" };
  }
  if (input.fixesThisRun >= input.cfg.maxFixesPerRun) {
    return { ok: false, reason: "run_budget" };
  }
  if (input.alreadySha) return { ok: false, reason: "sha_dedup" };
  if (!quietElapsed(input.pr.pushedAt, input.nowMs, input.cfg.minMinutesAfterPush)) {
    return { ok: false, reason: "quiet_period" };
  }

  const classified = classifyChecks(input.runs);
  if (input.runs.length > 0 && classified.pending) {
    return { ok: false, reason: "pending" };
  }
  const legacyPendingState = input.runs.length === 0 && legacyPending(input.combined);
  if (legacyPendingState) return { ok: false, reason: "pending" };

  const failed =
    classified.failedNames.length > 0 ||
    (input.runs.length === 0 && legacyFailure(input.combined) && !legacyPendingState);
  if (!failed) return { ok: false, reason: "not_failure" };

  const ignoredOnly =
    input.runs.length > 0 &&
    classified.failedRuns.length === 0 &&
    input.runs.every(
      (run) => run.conclusion !== null && IGNORE.has(run.conclusion),
    );
  if (ignoredOnly) return { ok: false, reason: "not_failure" };

  return {
    ok: true,
    failedNames:
      classified.failedNames.length > 0
        ? classified.failedNames
        : ["legacy-status"],
  };
}

/** G14: patch must stay inside the diagnostic directories and file cap. */
export function blastRadiusOk(
  changedFiles: string[],
  diagnosticsFiles: Array<string | null>,
  maxFiles: number,
): { ok: true } | { ok: false; reason: string } {
  if (changedFiles.length === 0) {
    return { ok: false, reason: "no files changed" };
  }
  if (changedFiles.length > maxFiles) {
    return {
      ok: false,
      reason: `blast radius ${changedFiles.length} > ${maxFiles}`,
    };
  }
  const dirs = new Set(
    diagnosticsFiles
      .filter((file): file is string => Boolean(file))
      .map((file) => file.split("/").slice(0, -1).join("/") || "."),
  );
  if (dirs.size === 0) return { ok: true };
  for (const file of changedFiles) {
    const dir = file.split("/").slice(0, -1).join("/") || ".";
    const related = [...dirs].some(
      (allowed) => dir === allowed || dir.startsWith(`${allowed}/`) || allowed === ".",
    );
    if (!related) {
      return { ok: false, reason: `unrelated file ${file}` };
    }
  }
  return { ok: true };
}
