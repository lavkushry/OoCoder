import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadDashboardModel, renderDashboard, writeDashboard } from "../dashboard.js";
import {
  closeDb,
  logEvent,
  recordCiAttempt,
  recordObservation,
  upsertCandidate,
} from "../state.js";
import type { IssueRef } from "../types.js";

function issue(n: number, statusNote = ""): IssueRef {
  return {
    owner: "acme",
    repo: "app",
    number: n,
    url: `https://github.com/acme/app/issues/${n}`,
    title: `issue ${n} ${statusNote}`,
    body: "",
    labels: ["bug"],
    comments: 0,
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-02T00:00:00Z",
    score: n,
  };
}

test("aggregations match a seeded history", () => {
  const home = mkdtempSync(join(tmpdir(), "oocoder-dash-"));
  upsertCandidate(issue(1), home);
  upsertCandidate(issue(2), home);
  recordObservation("acme/app", "issue", 4, home);
  recordObservation("acme/app", "skip:quota", 2, home);
  recordCiAttempt("acme/app", 2, "sha", "patched", "", home);
  logEvent("lesson", "acme/app", { lesson: "prefer docs" }, home);
  logEvent("pr_state", "acme/app", { state: "merged", number: 2 }, home);
  const model = loadDashboardModel(home, 30, new Date("2026-11-09T00:00:00Z"));
  assert.equal(model.kpis.actionsTotal, 2);
  assert.equal(model.funnel[0]?.n, 4);
  assert.equal(model.funnel[3]?.n, 1);
  assert.equal(model.fixes[0]?.outcome, "patched");
  assert.equal(model.friction[0]?.reason, "quota");
  assert.equal(model.lessons.length, 1);
  closeDb();
});

test("html has eight sections and no external chart host", async () => {
  const home = mkdtempSync(join(tmpdir(), "oocoder-html-"));
  upsertCandidate(issue(3), home);
  const out = join(home, "stats.html");
  await writeDashboard({ home, out, days: 30, refresh: false });
  const html = readFileSync(out, "utf8");
  for (const id of ["kpi", "timeline", "funnel", "repos", "prs", "fixes", "friction", "lessons"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  const dataMatch = /<script type="application\/json" id="oocoder-data">([\s\S]*?)<\/script>/.exec(html);
  assert.ok(dataMatch?.[1]);
  const data = JSON.parse(dataMatch[1]) as { kpis: { actionsTotal: number } };
  assert.equal(data.kpis.actionsTotal, 1);
  const withoutLinks = html.replace(/href="https:\/\/github\.com[^"]*"/g, "");
  assert.equal(/https?:\/\//.test(withoutLinks), false);
  closeDb();
});

test("render works when fix attempts are absent from the model", () => {
  const html = renderDashboard({
    generatedAt: "2026-11-09T00:00:00Z",
    days: 30,
    kpis: {
      actionsTotal: 0,
      actionsToday: 0,
      prsOpened: 0,
      merged: 0,
      acceptanceRate: 0,
      reposActive: 0,
      cooldownsActive: 0,
    },
    timeline: [],
    funnel: [
      { stage: "observed", n: 0 },
      { stage: "acted", n: 0 },
      { stage: "pr", n: 0 },
      { stage: "merged", n: 0 },
    ],
    repos: [],
    prs: [],
    fixes: [],
    friction: [],
    lessons: [],
  }, "/* no chart host */");
  assert.match(html, /id="fixes"/);
});
