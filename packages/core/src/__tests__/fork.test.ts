import assert from "node:assert/strict";
import test from "node:test";
import {
  forkRepoArgs,
  hasUsableCursorAuth,
  isPlaceholderSecret,
} from "../github.js";

test("forkRepoArgs omits --remote flags that gh rejects with a repo arg", () => {
  const args = forkRepoArgs("redis/redis");
  assert.deepEqual(args, [
    "repo",
    "fork",
    "redis/redis",
    "--default-branch-only",
  ]);
  assert.ok(!args.includes("--remote"));
  assert.ok(!args.includes("--remote=false"));
});

test("placeholder Cursor keys are rejected so Grok can run", () => {
  assert.equal(isPlaceholderSecret("…"), true);
  assert.equal(isPlaceholderSecret("..."), true);
  assert.equal(isPlaceholderSecret("changeme"), true);
  assert.equal(isPlaceholderSecret("key_live_abc123"), false);

  const prev = process.env.CURSOR_API_KEY;
  process.env.CURSOR_API_KEY = "…";
  delete process.env.CURSOR_API_KEY_FILE;
  delete process.env.CURSOR_SERVICE_ACCOUNT_KEY;
  try {
    assert.equal(hasUsableCursorAuth(), false);
  } finally {
    if (prev === undefined) delete process.env.CURSOR_API_KEY;
    else process.env.CURSOR_API_KEY = prev;
  }
});
