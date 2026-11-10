import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { noteObservation, noteSkip, noteUsage } from "../telemetry.js";
import { closeDb, getDb } from "../state.js";

function homeWithTelemetry(enabled: boolean): string {
  const home = mkdtempSync(join(tmpdir(), "oocoder-tel-"));
  writeFileSync(
    join(home, "policy.yaml"),
    `telemetry:\n  enabled: ${enabled}\n`,
    "utf8",
  );
  writeFileSync(join(home, "allowlist.yaml"), "orgs: []\nrepos: []\nstrict: true\n", "utf8");
  return home;
}

test("telemetry hooks write observation, skip, and usage rows", () => {
  const home = homeWithTelemetry(true);
  noteObservation("acme/app", "issue", 3, home);
  noteSkip("acme/app", "quota", home);
  noteUsage(10, 4, home);
  const db = getDb(home);
  const observations = db.prepare(`SELECT COUNT(*) AS n FROM observations`).get() as { n: number };
  const usage = db.prepare(`SELECT prompt_tokens AS n FROM usage`).get() as { n: number };
  assert.equal(Number(observations.n), 2);
  assert.equal(Number(usage.n), 10);
  closeDb();
});

test("telemetry false writes nothing", () => {
  const home = homeWithTelemetry(false);
  noteObservation("acme/app", "issue", 3, home);
  noteSkip("acme/app", "quota", home);
  noteUsage(10, 4, home);
  const db = getDb(home);
  const observations = db.prepare(`SELECT COUNT(*) AS n FROM observations`).get() as { n: number };
  const usage = db.prepare(`SELECT COUNT(*) AS n FROM usage`).get() as { n: number };
  assert.equal(Number(observations.n), 0);
  assert.equal(Number(usage.n), 0);
  closeDb();
});
