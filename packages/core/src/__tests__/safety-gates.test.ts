import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { filterAllowlistedIssues, isAllowed } from "../config.js";
import { ensureRepairWorktree } from "../git.js";
import type { AllowlistConfig } from "../types.js";
import { makefileVerifyArgs } from "../quality.js";
import {
  rateBumpsForLocalWork,
  verificationBlocksShip,
} from "../work.js";

test("daemon allowlist filter keeps only isAllowed repos", () => {
  const allowlist: AllowlistConfig = {
    orgs: ["acme"],
    repos: ["other/app"],
    strict: true,
  };
  const issues = [
    { owner: "acme", repo: "one", number: 1 },
    { owner: "evil", repo: "two", number: 2 },
    { owner: "other", repo: "app", number: 3 },
  ];
  assert.equal(isAllowed("evil", "two", allowlist), false);
  const kept = filterAllowlistedIssues(issues, allowlist);
  assert.deepEqual(
    kept.map((i) => `${i.owner}/${i.repo}`),
    ["acme/one", "other/app"],
  );
});

test("empty strict allowlist admits nothing", () => {
  const allowlist: AllowlistConfig = { orgs: [], repos: [], strict: true };
  assert.equal(isAllowed("acme", "one", allowlist), false);
  assert.deepEqual(
    filterAllowlistedIssues([{ owner: "acme", repo: "one" }], allowlist),
    [],
  );
});

test("verificationBlocksShip is hard when verify ran and failed", () => {
  assert.equal(verificationBlocksShip({ ran: true, ok: false }), true);
  assert.equal(verificationBlocksShip({ ran: true, ok: true }), false);
  assert.equal(verificationBlocksShip({ ran: false, ok: false }), false);
  assert.equal(verificationBlocksShip({ ran: false, ok: true }), false);
});

test("makefile verify defaults to compile-only make, not make test", () => {
  assert.equal(makefileVerifyArgs({}).command, "make");
  assert.deepEqual(makefileVerifyArgs({}).args, []);
  assert.equal(makefileVerifyArgs({ OOCODER_VERIFY_MAKE_TEST: "1" }).command, "make test");
});

test("rate bumps only after successful local actions", () => {
  assert.deepEqual(
    rateBumpsForLocalWork({ dryRun: true, committed: true, prOpened: true }),
    [],
  );
  assert.deepEqual(
    rateBumpsForLocalWork({ dryRun: false, committed: false, prOpened: false }),
    [],
  );
  assert.deepEqual(
    rateBumpsForLocalWork({ dryRun: false, committed: true, prOpened: false }),
    ["issues"],
  );
  assert.deepEqual(
    rateBumpsForLocalWork({ dryRun: false, committed: true, prOpened: true }),
    ["issues", "prs"],
  );
});

test("ensureRepairWorktree fails closed without process.cwd fallback", async () => {
  const home = mkdtempSync(join(tmpdir(), "oocoder-repair-"));
  await assert.rejects(
    () =>
      ensureRepairWorktree({
        owner: "acme",
        repo: "missing",
        number: 99,
        branch: "oocoder/fix-99",
        headOwner: "me",
        home,
      }),
    /will not use process\.cwd/,
  );
});

test("ensureRepairWorktree accepts an explicit existing cwd", async () => {
  const home = mkdtempSync(join(tmpdir(), "oocoder-repair-ok-"));
  const cwd = mkdtempSync(join(tmpdir(), "oocoder-wt-"));
  mkdirSync(cwd, { recursive: true });
  writeFileSync(join(cwd, "README"), "ok", "utf8");
  const resolved = await ensureRepairWorktree({
    owner: "acme",
    repo: "app",
    number: 1,
    branch: "oocoder/fix-1",
    headOwner: "me",
    home,
    explicitCwd: cwd,
  });
  assert.equal(resolved, cwd);
});
