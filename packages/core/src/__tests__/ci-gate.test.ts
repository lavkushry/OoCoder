import assert from "node:assert/strict";
import test from "node:test";
import { blastRadiusOk, gateCiCandidate, type PullSnapshot } from "../ciGate.js";
import type { CiFixConfig } from "../types.js";

const cfg: CiFixConfig = {
  enabled: true,
  maxAttemptsPerPr: 2,
  maxFixesPerRun: 1,
  minMinutesAfterPush: 10,
  maxFilesTouched: 3,
  maxLogBytes: 1000,
};

function pr(overrides: Partial<PullSnapshot> = {}): PullSnapshot {
  return {
    number: 42,
    state: "open",
    draft: false,
    merged: false,
    title: "fix",
    htmlUrl: "https://github.com/acme/app/pull/42",
    updatedAt: "2026-01-01T00:00:00Z",
    userLogin: "me",
    headRef: "oocoder/fix-42",
    headSha: "abc",
    headOwner: "me",
    baseRef: "main",
    pushedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const now = Date.parse("2026-01-01T00:30:00Z");

function gate(overrides: Partial<Parameters<typeof gateCiCandidate>[0]> = {}) {
  return gateCiCandidate({
    cfg,
    pr: pr(),
    login: "me",
    runs: [{ id: 1, name: "test", status: "completed", conclusion: "failure" }],
    combined: { state: "failure" },
    nowMs: now,
    alreadySha: false,
    attemptCount: 0,
    fixesThisRun: 0,
    alreadyStoodDown: false,
    ...overrides,
  });
}

test("rejects other authors and non-namespaced heads", () => {
  assert.equal(gate({ pr: pr({ userLogin: "other" }) }).ok, false);
  assert.equal(gate({ pr: pr({ headRef: "feature/x" }) }).ok, false);
  assert.equal(gate({ pr: pr({ headOwner: "upstream" }) }).ok, false);
});

test("skips pending and cancelled checks", () => {
  assert.equal(
    gate({ runs: [{ id: 1, name: "test", status: "in_progress", conclusion: null }] }).ok,
    false,
  );
  const cancelled = gate({
    runs: [{ id: 1, name: "test", status: "completed", conclusion: "cancelled" }],
  });
  assert.equal(cancelled.ok, false);
  if (!cancelled.ok) assert.equal(cancelled.reason, "not_failure");
});

test("skips quiet period, duplicate sha, and attempt cap", () => {
  const quiet = gate({ pr: pr({ pushedAt: "2026-01-01T00:25:00Z" }) });
  assert.equal(quiet.ok, false);
  if (!quiet.ok) assert.equal(quiet.reason, "quiet_period");
  const dedup = gate({ alreadySha: true });
  assert.equal(dedup.ok, false);
  if (!dedup.ok) assert.equal(dedup.reason, "sha_dedup");
  const cap = gate({ attemptCount: 2 });
  assert.equal(cap.ok, false);
  if (!cap.ok) assert.equal(cap.reason, "attempt_cap");
});

test("blast radius rejects unrelated files", () => {
  const bad = blastRadiusOk(["docs/other.md"], ["src/a.py"], 3);
  assert.equal(bad.ok, false);
  const good = blastRadiusOk(["src/a.py"], ["src/a.py"], 3);
  assert.equal(good.ok, true);
});
