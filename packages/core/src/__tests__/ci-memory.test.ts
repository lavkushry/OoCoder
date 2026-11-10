import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  ciAttemptCount,
  ciAttemptedSha,
  closeDb,
  getDb,
  recordCiAttempt,
} from "../state.js";

test("ci_fix_attempts migrates on a fresh database", () => {
  const home = mkdtempSync(join(tmpdir(), "oocoder-mem-"));
  const db = getDb(home);
  const tables = db
    .prepare(`SELECT name FROM sqlite_master WHERE type='table' AND name='ci_fix_attempts'`)
    .get() as { name: string } | undefined;
  assert.equal(tables?.name, "ci_fix_attempts");
  recordCiAttempt("acme/app", 7, "sha1", "patched", "ok", home);
  recordCiAttempt("acme/app", 7, "sha1", "dry_run", "preview", home);
  assert.equal(ciAttemptCount("acme/app", 7, home), 1);
  assert.equal(ciAttemptedSha("acme/app", 7, "sha1", home), true);
  assert.equal(ciAttemptedSha("acme/app", 7, "other", home), false);
  closeDb();
});
