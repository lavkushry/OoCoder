import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  campaignDefaults,
  campaignRuleLogPath,
  formatCampaignLearningEntry,
  formatCampaignMonitor,
  formatCampaignStatus,
  formatCampaignWatchTick,
  getCampaignStatus,
  isClaCheckName,
  logCampaignLearning,
  parseCampaignPrUrl,
  type CampaignMonitorResult,
  type CampaignWatchTickResult,
} from "../campaign.js";
import { closeDb, getDb, upsertCandidate, updateCandidate } from "../state.js";
import type { IssueRef, PolicyConfig } from "../types.js";

function writeHome(home: string): void {
  mkdirSync(home, { recursive: true });
  writeFileSync(
    join(home, "allowlist.yaml"),
    "orgs: []\nrepos:\n  - apache/airflow\nstrict: true\n",
    "utf8",
  );
  writeFileSync(
    join(home, "policy.yaml"),
    [
      "labels: [documentation]",
      "quality: { maxFilesChanged: 20, maxLinesChanged: 400 }",
      "rateLimits: { maxIssuesPerDay: 60, maxPrsPerDay: 50, maxCommentsPerDay: 40 }",
      "budget: { maxUsdPerRun: 5 }",
      'disclosure: ""',
      'prFooter: ""',
      "ciFix: { enabled: false, maxAttemptsPerPr: 1, maxFixesPerRun: 1, minMinutesAfterPush: 1, maxFilesTouched: 1, maxLogBytes: 1000 }",
      "telemetry: { enabled: false }",
      "dashboard: { refreshPrStates: false, historyDays: 7 }",
      "campaign: { targetPrs: 50, batchSize: 10, discoverMultiplier: 5, scrubBranding: true }",
      "",
    ].join("\n"),
    "utf8",
  );
}

function issue(partial: Partial<IssueRef> & Pick<IssueRef, "number" | "title">): IssueRef {
  return {
    owner: "apache",
    repo: "airflow",
    url: `https://github.com/apache/airflow/issues/${partial.number}`,
    body: "",
    labels: ["documentation"],
    comments: 0,
    createdAt: "2026-11-10T00:00:00.000Z",
    updatedAt: "2026-11-10T00:00:00.000Z",
    score: 20,
    ...partial,
  };
}

test("campaignDefaults reads policy campaign block", () => {
  const policy = {
    campaign: {
      targetPrs: 50,
      batchSize: 12,
      discoverMultiplier: 4,
      scrubBranding: false,
    },
  } as PolicyConfig;
  assert.deepEqual(campaignDefaults(policy), {
    targetPrs: 50,
    batchSize: 12,
    discoverMultiplier: 4,
    scrubBranding: false,
  });
});

test("getCampaignStatus counts pr_opened toward target", () => {
  const home = mkdtempSync(join(tmpdir(), "oocoder-campaign-"));
  writeHome(home);
  closeDb();
  getDb(home);
  const a = upsertCandidate(issue({ number: 1, title: "one" }), home);
  updateCandidate(
    a.id,
    { status: "pr_opened", prUrl: "https://github.com/apache/airflow/pull/1" },
    home,
  );
  const b = upsertCandidate(issue({ number: 2, title: "two" }), home);
  updateCandidate(
    b.id,
    { status: "pr_opened", prUrl: "https://github.com/apache/airflow/pull/2" },
    home,
  );
  upsertCandidate(issue({ number: 3, title: "three" }), home);

  const status = getCampaignStatus(home);
  assert.equal(status.targetPrs, 50);
  assert.equal(status.prsOpenedLifetime, 2);
  assert.equal(status.remainingToTarget, 48);
  assert.equal(status.byStatus.pr_opened, 2);
  assert.equal(status.byStatus.discovered, 1);
  assert.match(formatCampaignStatus(status), /2\/50/);
  closeDb();
});

test("getCampaignStatus keeps merged and closed PRs in the lifetime target", () => {
  const home = mkdtempSync(join(tmpdir(), "oocoder-campaign-"));
  writeHome(home);
  closeDb();
  getDb(home);
  const merged = upsertCandidate(issue({ number: 4, title: "merged" }), home);
  updateCandidate(
    merged.id,
    { status: "merged", prUrl: "https://github.com/duckdb/duckdb-web/pull/7708" },
    home,
  );
  const closed = upsertCandidate(issue({ number: 5, title: "closed" }), home);
  updateCandidate(
    closed.id,
    { status: "closed", prUrl: "https://github.com/duckdb/duckdb-web/pull/7712" },
    home,
  );
  const open = upsertCandidate(issue({ number: 6, title: "open" }), home);
  updateCandidate(
    open.id,
    { status: "pr_opened", prUrl: "https://github.com/duckdb/duckdb-web/pull/7802" },
    home,
  );
  const status = getCampaignStatus(home);
  assert.equal(status.prsOpenedLifetime, 3);
  assert.equal(status.remainingToTarget, 47);
  assert.equal(status.recentPrs.length, 1);
  assert.equal(status.recentPrs[0]?.prUrl, "https://github.com/duckdb/duckdb-web/pull/7802");
  closeDb();
});

test("parseCampaignPrUrl reads the pull request repo, not the issue repo", () => {
  assert.deepEqual(
    parseCampaignPrUrl("https://github.com/duckdb/duckdb-web/pull/7708"),
    { owner: "duckdb", repo: "duckdb-web", number: 7708 },
  );
  assert.equal(parseCampaignPrUrl("https://github.com/duckdb/duckdb/issues/26283"), null);
});

test("formatCampaignMonitor summarizes action items", () => {
  const result: CampaignMonitorResult = {
    checked: 3,
    waiting: 1,
    approved: [],
    needsAction: [
      {
        repo: "duckdb/duckdb-web",
        number: 7717,
        title: "docs: document GEOGRAPHY",
        url: "https://github.com/duckdb/duckdb-web/pull/7717",
        reviewDecision: "CHANGES_REQUESTED",
        mergeable: "MERGEABLE",
        attention: ["changes_requested", "review_comment"],
        notes: ["@szarnyasg requested changes: storage version"],
      },
    ],
  };
  const text = formatCampaignMonitor(result);
  assert.match(text, /1 need action/);
  assert.match(text, /duckdb\/duckdb-web#7717/);
  assert.match(text, /changes_requested/);
});

test("isClaCheckName detects CLA gates without flagging project CI", () => {
  assert.equal(isClaCheckName("cla-signed"), true);
  assert.equal(isClaCheckName("CLA Assistant"), true);
  assert.equal(isClaCheckName("contributor-license-agreement"), true);
  assert.equal(isClaCheckName("maven-checks 27"), false);
  assert.equal(isClaCheckName("build-success"), false);
});

test("formatCampaignLearningEntry requires repo title rule", () => {
  const { date, entry } = formatCampaignLearningEntry({
    repo: "redis/redis",
    title: "LPOP allocsize",
    rule: "Pass the real list key into addListRangeReply.",
    source: "#16227",
    date: "2026-11-10",
    encode: true,
  });
  assert.equal(date, "2026-11-10");
  assert.match(entry, /### redis\/redis — LPOP allocsize/);
  assert.match(entry, /#16227/);
  assert.match(entry, /implement\.ts/);
  assert.throws(() =>
    formatCampaignLearningEntry({ repo: "", title: "x", rule: "y" }),
  );
});

test("formatCampaignWatchTick includes tick header and status", () => {
  const tick: CampaignWatchTickResult = {
    tick: 2,
    synced: 3,
    scrubbed: 1,
    opened: 0,
    ciFixes: 0,
    messages: ["sync: 3 open PR(s)"],
    monitor: {
      checked: 3,
      waiting: 2,
      approved: [],
      needsAction: [],
    },
    status: {
      targetPrs: 100,
      prsOpenedLifetime: 100,
      prsOpenedToday: 10,
      remainingToTarget: 0,
      remainingToday: 90,
      rate: {
        issues: 0,
        prs: 10,
        comments: 0,
        maxIssues: 120,
        maxPrs: 100,
        maxComments: 80,
      },
      byStatus: { pr_opened: 100 },
      recentPrs: [],
    },
  };
  const text = formatCampaignWatchTick(tick);
  assert.match(text, /tick 2/);
  assert.match(text, /100\/100/);
});

test("logCampaignLearning appends under the day heading", () => {
  const root = mkdtempSync(join(tmpdir(), "oocoder-learn-"));
  const home = join(root, "home");
  writeHome(home);
  mkdirSync(join(root, "docs"), { recursive: true });
  writeFileSync(
    join(root, "docs", "CAMPAIGN_RULE_LOG.md"),
    "# Campaign rule log\n\n## 2026-11-10\n\n### already — here\n- **Rule:** prior\n\n",
    "utf8",
  );
  getDb(home);
  const result = logCampaignLearning({
    repo: "trinodb/trino",
    title: "CLA email PDF",
    rule: "Email signed CLA PDF to cla@trino.io.",
    source: "#32057",
    date: "2026-11-10",
    encode: true,
    projectRoot: root,
    home,
  });
  assert.equal(result.logPath, campaignRuleLogPath(root));
  const text = readFileSync(result.logPath, "utf8");
  assert.equal((text.match(/^## 2026-11-10$/gm) ?? []).length, 1);
  assert.match(text, /### trinodb\/trino — CLA email PDF/);
  assert.match(text, /cla@trino\.io/);
  closeDb();
});
