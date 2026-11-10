import assert from "node:assert/strict";
import test from "node:test";
import { parseErrors } from "../ciParse.js";

test("parses pytest, mypy, ruff, tsc, eslint, jest, go, rust, c, and generic", () => {
  const log = [
    "FAILED tests/test_parser.py::test_empty_input",
    "E   AssertionError: empty",
    "src/parser.py:12: error: bad type",
    "src/parser.py:33:1: E501 line too long",
    "src/app.ts(15,3): error TS2322: nope",
    "src/app.js: line 5, col 3, Error - unexpected",
    "● suite › nested",
    "Expected 1, received 2",
    "./pkg/x.go:42:3: undefined: foo",
    "--- FAIL: TestX",
    "error[E0308]: mismatched types",
    "  --> src/x.rs:10:5",
    "src/main.c:64:5: error: expected ';'",
    "lib/util.rb:9: something failed",
  ].join("\n");
  const found = parseErrors(log);
  assert.ok(found.some((item) => item.file === "tests/test_parser.py"));
  assert.ok(found.some((item) => item.file === "src/parser.py" && item.line === 12));
  assert.ok(found.some((item) => item.file === "src/app.ts" && item.line === 15));
  assert.ok(found.some((item) => item.file === "./pkg/x.go"));
  assert.ok(found.some((item) => item.file === "src/x.rs"));
  assert.ok(found.some((item) => item.file === "src/main.c"));
  assert.ok(found.some((item) => item.file === "lib/util.rb"));
  assert.ok(found.length >= 10);
});

test("dedupes identical file and line", () => {
  const log = "src/a.py:4: error: boom\nsrc/a.py:4: error: boom\n";
  const found = parseErrors(log);
  assert.equal(found.filter((item) => item.file === "src/a.py" && item.line === 4).length, 1);
});

test("empty input yields no diagnostics", () => {
  assert.deepEqual(parseErrors("   \n"), []);
});

test("unparsed log becomes a raw tail", () => {
  const found = parseErrors("worker exited without a stack\nretry later\n");
  assert.equal(found.length, 1);
  assert.equal(found[0]?.kind, "raw");
});
