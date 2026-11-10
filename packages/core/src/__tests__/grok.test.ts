import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { applyGrokEdits, parseGrokEdits, safeRepoPath } from "../grok.js";

test("parses a fenced Grok edit plan", () => {
  const plan = parseGrokEdits(
    '```json\n{"summary":"fix readme","files":[{"path":"README.md","content":"hello\\n"}]}\n```',
  );
  assert.equal(plan.summary, "fix readme");
  assert.equal(plan.files[0]?.path, "README.md");
});

test("writes files inside the repo and rejects traversal", () => {
  const cwd = mkdtempSync(join(tmpdir(), "oocoder-grok-"));
  const written = applyGrokEdits(cwd, {
    summary: "add file",
    files: [{ path: "docs/note.txt", content: "ok" }],
  });
  assert.deepEqual(written, ["docs/note.txt"]);
  assert.equal(readFileSync(join(cwd, "docs/note.txt"), "utf8"), "ok");
  assert.throws(() => safeRepoPath(cwd, "../outside.txt"));
});
