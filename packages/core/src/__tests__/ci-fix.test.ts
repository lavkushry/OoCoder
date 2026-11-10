import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { runCiFix, standDownBody, type CiApi } from "../ciFix.js";
import { closeDb, ciAttemptedSha, listCiAttempts, recordCiAttempt } from "../state.js";
import { pushArgs } from "../git.js";

function pull(overrides: Record<string, unknown> = {}) {
  return {
    number: 42,
    state: "open",
    draft: false,
    merged: false,
    title: "fix parser",
    html_url: "https://github.com/acme/app/pull/42",
    updated_at: "2026-01-01T00:00:00Z",
    user: { login: "me" },
    head: {
      ref: "oocoder/fix-42",
      sha: "sha-red",
      repo: { owner: { login: "me" }, pushed_at: "2026-01-01T00:00:00Z" },
    },
    base: { ref: "main" },
    ...overrides,
  };
}

function api(pullBody: Record<string, unknown> = pull()): CiApi & { comments: string[] } {
  const comments: string[] = [];
  return {
    comments,
    async getPull() {
      return pullBody;
    },
    async checkRuns() {
      return [{ id: 9, name: "test", status: "completed", conclusion: "failure" }];
    },
    async combinedStatus() {
      return { state: "failure" };
    },
    async annotations() {
      return [{ path: "src/parser.py", start_line: 88, message: "TypeError" }];
    },
    async actionsRuns() {
      return [];
    },
    async jobLogs() {
      return "";
    },
    async comment(_o, _r, _n, body) {
      comments.push(body);
      return "https://github.com/acme/app/pull/42#issuecomment-1";
    },
  };
}

const home = mkdtempSync(join(tmpdir(), "oocoder-fix-"));

test("red PR gets one non-force push on the same branch", async () => {
  const pushes: string[] = [];
  const fake = api();
  const result = await runCiFix({
    owner: "acme",
    repo: "app",
    number: 42,
    login: "me",
    home,
    api: fake,
    nowMs: Date.parse("2026-01-01T01:00:00Z"),
    cwd: "/tmp/wt",
    async repair() {
      return {
        changedFiles: ["src/parser.py"],
        testsPassed: true,
        ok: true,
        summary: "fixed",
      };
    },
    async push(_cwd, branch) {
      const args = pushArgs(branch);
      assert.equal(args.includes("--force"), false);
      pushes.push(branch);
    },
  });
  assert.equal(result.outcome, "patched");
  assert.deepEqual(pushes, ["oocoder/fix-42"]);
  assert.equal(ciAttemptedSha("acme/app", 42, "sha-red", home), true);
  closeDb();
});

test("blast radius consumes an attempt and does not push", async () => {
  const pushes: string[] = [];
  const result = await runCiFix({
    owner: "acme",
    repo: "app",
    number: 43,
    login: "me",
    home,
    api: api(
      pull({
        number: 43,
        html_url: "https://github.com/acme/app/pull/43",
        head: {
          ref: "oocoder/fix-43",
          sha: "sha-wide",
          repo: { owner: { login: "me" }, pushed_at: "2026-01-01T00:00:00Z" },
        },
      }),
    ),
    nowMs: Date.parse("2026-01-01T01:00:00Z"),
    async repair() {
      return {
        changedFiles: ["a.py", "b.py", "c.py", "d.py"],
        testsPassed: true,
        ok: true,
        summary: "too big",
      };
    },
    async push(_cwd, branch) {
      pushes.push(branch);
    },
  });
  assert.equal(result.ok, false);
  assert.equal(pushes.length, 0);
  assert.ok(listCiAttempts(home).some((row) => row.outcome === "no_fix"));
  closeDb();
});

test("attempt cap drafts a stand-down comment in dry-run", async () => {
  recordCiAttempt("acme/app", 77, "old-1", "no_fix", "first", home);
  recordCiAttempt("acme/app", 77, "old-2", "tests_failed", "second", home);
  const fake = api(
    pull({
      number: 77,
      html_url: "https://github.com/acme/app/pull/77",
      head: {
        ref: "oocoder/fix-77",
        sha: "sha-cap",
        repo: { owner: { login: "me" }, pushed_at: "2026-01-01T00:00:00Z" },
      },
    }),
  );
  const result = await runCiFix({
    owner: "acme",
    repo: "app",
    number: 77,
    login: "me",
    dryRun: true,
    home,
    api: fake,
    nowMs: Date.parse("2026-01-01T02:00:00Z"),
  });
  assert.equal(result.outcome, "gave_up");
  assert.match(result.standDown ?? "", /stopping/);
  assert.equal(fake.comments.length, 0);
  closeDb();
});

test("other authors are ignored", async () => {
  const result = await runCiFix({
    owner: "acme",
    repo: "app",
    number: 9,
    login: "me",
    home,
    api: api(
      pull({
        number: 9,
        user: { login: "someone" },
        head: {
          ref: "oocoder/fix-9",
          sha: "zzz",
          repo: { owner: { login: "someone" }, pushed_at: "2026-01-01T00:00:00Z" },
        },
      }),
    ),
    nowMs: Date.parse("2026-01-01T03:00:00Z"),
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "not_ours");
  closeDb();
});

test("stand-down template includes disclosure", () => {
  const body = standDownBody({
    attempts: 2,
    detail: "tests failed",
    disclosure: "AI agent",
  });
  assert.match(body, /AI agent/);
  assert.match(body, /Attempts used: 2/);
});
