import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { loadAllowlist, loadPolicy, isAllowed } from "./config.js";
import { discoverIssues } from "./discover.js";
import { checkAuth } from "./github.js";
import { findProjectRoot, oocoderHome } from "./paths.js";
import { run, runOk } from "./shell.js";
import {
  getRateCount,
  listCandidates,
  logEvent,
  updateCandidate,
  upsertCandidate,
} from "./state.js";
import { followUpCiFixes } from "./ciFix.js";
import type { CandidateRecord, IssueRef, PolicyConfig, RuntimeMode } from "./types.js";
import { workOnIssue } from "./work.js";

export interface CampaignConfig {
  /** Lifetime PR goal for this campaign. */
  targetPrs: number;
  /** Max PRs to open in one `campaign run` invocation. */
  batchSize: number;
  /** Extra discover pool multiplier vs remaining slots. */
  discoverMultiplier: number;
  /** Strip Cursor/OoCoder footers from open PR bodies after each batch. */
  scrubBranding: boolean;
}

export interface CampaignStatus {
  targetPrs: number;
  prsOpenedLifetime: number;
  prsOpenedToday: number;
  remainingToTarget: number;
  remainingToday: number;
  rate: {
    issues: number;
    prs: number;
    comments: number;
    maxIssues: number;
    maxPrs: number;
    maxComments: number;
  };
  byStatus: Record<string, number>;
  recentPrs: Array<{ id: string; prUrl: string; title: string }>;
}

export interface CampaignRunOptions {
  dryRun?: boolean;
  skipDiscuss?: boolean;
  skipVerify?: boolean;
  skipPr?: boolean;
  limit?: number;
  runtime?: "local" | "cloud";
  model?: string;
  home?: string;
}

export interface CampaignRunResult {
  status: CampaignStatus;
  discovered: number;
  processed: number;
  opened: number;
  failed: number;
  scrubbed: number;
  messages: string[];
}

const BRANDING_LINE_RE = /cursor\.com|made with\s*\[?cursor\]?|oocoder/i;

export function campaignDefaults(policy: PolicyConfig): CampaignConfig {
  const campaign = policy.campaign;
  return {
    targetPrs: campaign?.targetPrs ?? 50,
    batchSize: campaign?.batchSize ?? 10,
    discoverMultiplier: campaign?.discoverMultiplier ?? 5,
    scrubBranding: campaign?.scrubBranding ?? true,
  };
}

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function countByStatus(rows: CandidateRecord[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const row of rows) {
    out[row.status] = (out[row.status] ?? 0) + 1;
  }
  return out;
}

export function getCampaignStatus(home = oocoderHome()): CampaignStatus {
  const policy = loadPolicy(home);
  const cfg = campaignDefaults(policy);
  const rows = listCandidates(undefined, home);
  // Merged and closed rows were opened. Keep them in the lifetime target so
  // a later merge does not look like a missing PR and reopen the wave.
  const opened = rows.filter(
    (r) =>
      (r.status === "pr_opened" || r.status === "merged" || r.status === "closed") &&
      r.prUrl,
  );
  const prsOpenedLifetime = opened.length;
  // Daily cap must use the rate counter only. Candidate updatedAt is bumped by
  // campaign sync/monitor, so counting "open PRs touched today" falsely exhausts
  // the day and blocks continuous waves on Linux watch.
  const prsOpenedToday = getRateCount("prs", home);
  const remainingToTarget = Math.max(0, cfg.targetPrs - prsOpenedLifetime);
  const remainingToday = Math.max(0, policy.rateLimits.maxPrsPerDay - prsOpenedToday);
  return {
    targetPrs: cfg.targetPrs,
    prsOpenedLifetime,
    prsOpenedToday,
    remainingToTarget,
    remainingToday,
    rate: {
      issues: getRateCount("issues", home),
      prs: getRateCount("prs", home),
      comments: getRateCount("comments", home),
      maxIssues: policy.rateLimits.maxIssuesPerDay,
      maxPrs: policy.rateLimits.maxPrsPerDay,
      maxComments: policy.rateLimits.maxCommentsPerDay,
    },
    byStatus: countByStatus(rows),
    recentPrs: rows
      .filter((r) => r.status === "pr_opened" && r.prUrl)
      .slice(0, 15)
      .map((r) => ({
        id: r.id,
        prUrl: r.prUrl!,
        title: r.title,
      })),
  };
}

type GhPrHit = {
  number: number;
  title: string;
  url: string;
  createdAt: string;
  repository: { nameWithOwner: string };
};

/** Import allowlisted open PRs authored by the signed-in user into the campaign DB. */
export async function syncCampaignPrs(
  home = oocoderHome(),
): Promise<{ synced: number; prs: GhPrHit[] }> {
  const auth = await checkAuth();
  if (!auth.ghLogin) {
    throw new Error(auth.message);
  }
  const allowlist = loadAllowlist(home);
  const raw = await runOk("gh", [
    "search",
    "prs",
    "--author",
    auth.ghLogin,
    "--state",
    "open",
    "--limit",
    "100",
    "--json",
    "number,title,url,createdAt,repository",
  ]);
  const hits = JSON.parse(raw) as GhPrHit[];
  let synced = 0;
  for (const pr of hits) {
    const full = pr.repository?.nameWithOwner;
    if (!full || !full.includes("/")) continue;
    const [owner, repo] = full.split("/", 2) as [string, string];
    if (!isAllowed(owner, repo, allowlist)) continue;
    const issueLike: IssueRef = {
      owner,
      repo,
      number: pr.number,
      url: pr.url.replace("/pull/", "/issues/"),
      title: pr.title,
      body: "",
      labels: [],
      comments: 0,
      createdAt: pr.createdAt,
      updatedAt: new Date().toISOString(),
      score: 0,
    };
    const row = upsertCandidate(issueLike, home);
    updateCandidate(
      row.id,
      {
        status: "pr_opened",
        prUrl: pr.url,
        notes: row.notes ?? "synced from GitHub open PRs",
      },
      home,
    );
    synced += 1;
  }
  logEvent("campaign_sync", auth.ghLogin, { synced, total: hits.length }, home);
  return { synced, prs: hits.filter((pr) => {
    const full = pr.repository?.nameWithOwner ?? "";
    const [owner, repo] = full.split("/", 2);
    return owner && repo ? isAllowed(owner, repo, allowlist) : false;
  }) };
}

function scrubBody(body: string): { next: string; changed: boolean } {
  const lines = body.split(/\r?\n/);
  const kept = lines.filter((ln) => !BRANDING_LINE_RE.test(ln));
  const next = kept.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd() + "\n";
  return { next, changed: next !== body && next !== `${body.trimEnd()}\n` };
}

/** Remove Cursor/OoCoder marketing footers from allowlisted open PR bodies. */
export async function scrubCampaignBranding(
  home = oocoderHome(),
): Promise<{ scrubbed: number; checked: number }> {
  const auth = await checkAuth();
  if (!auth.ghLogin) {
    throw new Error(auth.message);
  }
  const allowlist = loadAllowlist(home);
  const raw = await runOk("gh", [
    "search",
    "prs",
    "--author",
    auth.ghLogin,
    "--state",
    "open",
    "--limit",
    "100",
    "--json",
    "number,url,repository,body",
  ]);
  const hits = JSON.parse(raw) as Array<
    GhPrHit & { body?: string }
  >;
  let checked = 0;
  let scrubbed = 0;
  for (const pr of hits) {
    const full = pr.repository?.nameWithOwner;
    if (!full) continue;
    const [owner, repo] = full.split("/", 2) as [string, string];
    if (!isAllowed(owner, repo, allowlist)) continue;
    checked += 1;
    const body = pr.body ?? "";
    if (!BRANDING_LINE_RE.test(body)) continue;
    const { next, changed } = scrubBody(body);
    if (!changed) continue;
    const edit = await run("gh", [
      "pr",
      "edit",
      String(pr.number),
      "--repo",
      full,
      "--body",
      next,
    ]);
    if (edit.code === 0) {
      scrubbed += 1;
      logEvent(
        "campaign_scrub",
        `${full}#${pr.number}`,
        { url: pr.url },
        home,
      );
    }
  }
  return { scrubbed, checked };
}

function alreadyBusy(home: string): Set<string> {
  return new Set(
    listCandidates(undefined, home)
      .filter((c) => c.status === "pr_opened" || c.status === "working")
      .map((c) => c.id),
  );
}

/** Discover → work loop toward the campaign target, respecting daily rate limits. */
export async function runCampaign(
  opts: CampaignRunOptions = {},
): Promise<CampaignRunResult> {
  const home = opts.home ?? oocoderHome();
  const policy = loadPolicy(home);
  const cfg = campaignDefaults(policy);
  const messages: string[] = [];
  const statusBefore = getCampaignStatus(home);

  if (statusBefore.remainingToTarget <= 0) {
    messages.push(
      `Campaign target already met: ${statusBefore.prsOpenedLifetime}/${statusBefore.targetPrs} PRs.`,
    );
    return {
      status: statusBefore,
      discovered: 0,
      processed: 0,
      opened: 0,
      failed: 0,
      scrubbed: 0,
      messages,
    };
  }
  if (statusBefore.remainingToday <= 0) {
    messages.push(
      `Daily PR cap reached: ${statusBefore.prsOpenedToday}/${statusBefore.rate.maxPrs}.`,
    );
    return {
      status: statusBefore,
      discovered: 0,
      processed: 0,
      opened: 0,
      failed: 0,
      scrubbed: 0,
      messages,
    };
  }

  if (!opts.dryRun) {
    const auth = await checkAuth();
    if (!auth.cursorApiKey && !auth.grokApiKey) {
      messages.push(
        "Cannot open PRs: set CURSOR_API_KEY (or CURSOR_API_KEY_FILE) or GROK_API_KEY / XAI_API_KEY. On Linux watch, put the key in /etc/oocoder/campaign.env.",
      );
      return {
        status: statusBefore,
        discovered: 0,
        processed: 0,
        opened: 0,
        failed: 0,
        scrubbed: 0,
        messages,
      };
    }
  }

  const batch = Math.min(
    opts.limit ?? cfg.batchSize,
    statusBefore.remainingToTarget,
    statusBefore.remainingToday,
  );
  const discoverLimit = Math.max(batch * cfg.discoverMultiplier, 40);
  messages.push(
    `Campaign run: open up to ${batch} PR(s) (target ${statusBefore.targetPrs}, lifetime ${statusBefore.prsOpenedLifetime}, today ${statusBefore.prsOpenedToday}/${statusBefore.rate.maxPrs}).`,
  );

  const issues = await discoverIssues({ limit: discoverLimit });
  const allowlist = loadAllowlist(home);
  const actionable = issues.filter((i) => isAllowed(i.owner, i.repo, allowlist));
  const busy = alreadyBusy(home);

  let processed = 0;
  let opened = 0;
  let failed = 0;

  for (const issue of actionable) {
    if (processed >= batch) break;
    const id = `${issue.owner}/${issue.repo}#${issue.number}`;
    if (busy.has(id)) {
      messages.push(`Skip ${id} (already working/pr_opened)`);
      continue;
    }
    messages.push(`Work ${id}`);
    try {
      const result = await workOnIssue({
        issueUrl: issue.url,
        dryRun: opts.dryRun,
        skipDiscuss: opts.skipDiscuss ?? true,
        skipVerify: opts.skipVerify ?? true,
        skipPr: opts.skipPr,
        runtime: opts.runtime ?? "local",
        model: opts.model,
      });
      processed += 1;
      if (result.prUrl) {
        opened += 1;
        busy.add(id);
        messages.push(`Opened ${result.prUrl}`);
      } else {
        messages.push(result.message);
      }
    } catch (err) {
      failed += 1;
      const msg = err instanceof Error ? err.message : String(err);
      messages.push(`Failed ${id}: ${msg}`);
      if (/Rate limit reached for prs/i.test(msg)) {
        messages.push("Stopping: daily PR rate limit hit.");
        break;
      }
    }
  }

  let scrubbed = 0;
  if (cfg.scrubBranding && !opts.dryRun) {
    try {
      const scrub = await scrubCampaignBranding(home);
      scrubbed = scrub.scrubbed;
      messages.push(`Scrubbed branding on ${scrubbed}/${scrub.checked} open PR(s).`);
    } catch (err) {
      messages.push(
        `Scrub skipped: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  const status = getCampaignStatus(home);
  logEvent(
    "campaign_run",
    null,
    { processed, opened, failed, scrubbed, batch },
    home,
  );
  messages.push(
    `Progress: ${status.prsOpenedLifetime}/${status.targetPrs} lifetime, ${status.prsOpenedToday}/${status.rate.maxPrs} today.`,
  );
  return {
    status,
    discovered: actionable.length,
    processed,
    opened,
    failed,
    scrubbed,
    messages,
  };
}

export function formatCampaignStatus(status: CampaignStatus): string {
  const lines = [
    `Campaign target: ${status.prsOpenedLifetime}/${status.targetPrs} PRs (remaining ${status.remainingToTarget})`,
    `Today: ${status.prsOpenedToday}/${status.rate.maxPrs} PRs (remaining ${status.remainingToday})`,
    `Rate counters: issues=${status.rate.issues}/${status.rate.maxIssues} prs=${status.rate.prs}/${status.rate.maxPrs} comments=${status.rate.comments}/${status.rate.maxComments}`,
    `Candidates: ${Object.entries(status.byStatus)
      .map(([k, v]) => `${k}=${v}`)
      .join(" ") || "(none)"}`,
  ];
  if (status.recentPrs.length > 0) {
    lines.push("Recent PRs:");
    for (const pr of status.recentPrs.slice(0, 10)) {
      lines.push(`  ${pr.id}  ${pr.prUrl}`);
    }
  }
  return lines.join("\n");
}

export type CampaignMonitorAttention =
  | "changes_requested"
  | "human_comment"
  | "review_comment"
  | "ci_failing"
  | "cla_blocking"
  | "monitor_error"
  | "approved";

/** True when a check name is a CLA / license-agreement gate, not project CI. */
export function isClaCheckName(name: string): boolean {
  return /cla|contributor[\s_-]*license|license[\s_-]*agreement/i.test(name);
}

export interface CampaignMonitorItem {
  repo: string;
  number: number;
  title: string;
  url: string;
  reviewDecision: string;
  mergeable: string | null;
  attention: CampaignMonitorAttention[];
  notes: string[];
}

export interface CampaignMonitorResult {
  checked: number;
  needsAction: CampaignMonitorItem[];
  approved: CampaignMonitorItem[];
  waiting: number;
  /** Campaign PRs that merged during this pass. */
  merged?: number;
  /** Campaign PRs closed without merge during this pass. */
  closed?: number;
}

/** Pull URL from a campaign row. The issue repo can differ from the PR repo. */
export function parseCampaignPrUrl(
  url: string,
): { owner: string; repo: string; number: number } | null {
  const match = /github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/.exec(url);
  if (!match) return null;
  return { owner: match[1] ?? "", repo: match[2] ?? "", number: Number(match[3]) };
}

function campaignPrsToWatch(home: string): Array<GhPrHit & { candidateIds: string[] }> {
  const grouped = new Map<string, GhPrHit & { candidateIds: string[] }>();
  for (const row of listCandidates("pr_opened", home)) {
    if (!row.prUrl) continue;
    const parsed = parseCampaignPrUrl(row.prUrl);
    if (!parsed) continue;
    const key = `${parsed.owner}/${parsed.repo}#${parsed.number}`;
    const existing = grouped.get(key);
    if (existing) {
      existing.candidateIds.push(row.id);
      continue;
    }
    grouped.set(key, {
      number: parsed.number,
      title: row.title,
      url: row.prUrl,
      createdAt: row.updatedAt,
      repository: { nameWithOwner: `${parsed.owner}/${parsed.repo}` },
      candidateIds: [row.id],
    });
  }
  return [...grouped.values()];
}

const BOT_LOGINS = new Set([
  "github-actions",
  "github-actions[bot]",
  "netlify",
  "netlify[bot]",
  "mintlify",
  "mintlify[bot]",
  "sourcery-ai",
  "sourcery-ai[bot]",
  "chatgpt-codex-connector",
  "chatgpt-codex-connector[bot]",
  "copilot-pull-request-reviewer",
  "copilot-pull-request-reviewer[bot]",
  "cla-assistant",
  "cla-assistant[bot]",
  "claassistant",
  "claassistant[bot]",
  "vercel",
  "vercel[bot]",
  "codecov",
  "codecov[bot]",
  "dependabot",
  "dependabot[bot]",
  "cursor",
  "cursor[bot]",
  "clickhouse-gh",
  "clickhouse-gh[bot]",
]);

function isAutomationComment(body: string): boolean {
  const trimmed = body.trimStart();
  return (
    trimmed.startsWith("<!-- CI automatic comment") ||
    trimmed.startsWith("<!-- codex-pull-request-review-summary") ||
    trimmed.startsWith("<!-- codex-review-state")
  );
}

function isBotLogin(login: string): boolean {
  const lower = login.toLowerCase();
  if (BOT_LOGINS.has(lower) || lower.endsWith("[bot]")) return true;
  // Catch unnamed automation accounts without matching human maintainers.
  return /(^|[-_])bot([-_]|$)/i.test(lower) || lower.includes("codecov");
}

/**
 * Scan allowlisted open PRs for maintainer feedback that needs a rework
 * (CHANGES_REQUESTED, human review comments, failing CI).
 */
export async function monitorCampaignPrs(
  home = oocoderHome(),
): Promise<CampaignMonitorResult> {
  const auth = await checkAuth();
  if (!auth.ghLogin) {
    throw new Error(auth.message);
  }
  const allowlist = loadAllowlist(home);
  const me = auth.ghLogin.toLowerCase();
  // Watch every campaign PR still marked open. GitHub search is capped at 100
  // and this account has hundreds of other open PRs, so search drops older ones.
  const hits = campaignPrsToWatch(home);
  const needsAction: CampaignMonitorItem[] = [];
  const approved: CampaignMonitorItem[] = [];
  let checked = 0;
  let waiting = 0;
  let merged = 0;
  let closed = 0;

  for (const pr of hits) {
    const full = pr.repository?.nameWithOwner;
    if (!full || !full.includes("/")) continue;
    const [owner, repo] = full.split("/", 2) as [string, string];
    if (!isAllowed(owner, repo, allowlist)) continue;
    checked += 1;

    let meta: {
      state?: string;
      reviewDecision?: string;
      latestReviews?: Array<{
        author?: { login?: string };
        state?: string;
        body?: string;
        submittedAt?: string;
      }>;
      comments?: Array<{ author?: { login?: string }; body?: string; createdAt?: string }>;
      statusCheckRollup?: Array<{ state?: string; conclusion?: string; name?: string }>;
      mergeable?: string;
      url?: string;
      title?: string;
    };
    try {
      const metaRaw = await runOk("gh", [
        "pr",
        "view",
        String(pr.number),
        "--repo",
        full,
        "--json",
        "state,reviewDecision,latestReviews,comments,statusCheckRollup,mergeable,url,title",
      ]);
      meta = JSON.parse(metaRaw) as typeof meta;
    } catch (err) {
      needsAction.push({
        repo: full,
        number: pr.number,
        title: pr.title,
        url: pr.url,
        reviewDecision: "",
        mergeable: null,
        attention: ["monitor_error"],
        notes: [
          `monitor fetch failed: ${err instanceof Error ? err.message : String(err)}`.slice(
            0,
            200,
          ),
        ],
      });
      continue;
    }

    const ghState = (meta.state ?? "").toUpperCase();
    if (ghState === "MERGED" || ghState === "CLOSED") {
      const next = ghState === "MERGED" ? "merged" : "closed";
      for (const id of pr.candidateIds) {
        updateCandidate(id, { status: next }, home);
      }
      if (next === "merged") merged += 1;
      else closed += 1;
      continue;
    }

    const attention: CampaignMonitorAttention[] = [];
    const notes: string[] = [];
    const decision = meta.reviewDecision ?? "";

    if (decision === "CHANGES_REQUESTED") {
      attention.push("changes_requested");
      notes.push("Review decision: CHANGES_REQUESTED");
    } else if (decision === "APPROVED") {
      attention.push("approved");
    }

    // Author's latest conversation activity: used to suppress already-answered
    // issue/review comments so monitor stays focused on fresh feedback.
    let myLastActivityMs = 0;
    for (const comment of meta.comments ?? []) {
      const login = comment.author?.login ?? "";
      if (login.toLowerCase() !== me) continue;
      const t = Date.parse(comment.createdAt ?? "");
      if (!Number.isNaN(t) && t > myLastActivityMs) myLastActivityMs = t;
    }
    for (const review of meta.latestReviews ?? []) {
      const login = review.author?.login ?? "";
      if (login.toLowerCase() !== me) continue;
      const t = Date.parse(review.submittedAt ?? "");
      if (!Number.isNaN(t) && t > myLastActivityMs) myLastActivityMs = t;
    }

    for (const review of meta.latestReviews ?? []) {
      const login = review.author?.login ?? "";
      if (!login || login.toLowerCase() === me || isBotLogin(login)) continue;
      if (review.state === "CHANGES_REQUESTED") {
        if (!attention.includes("changes_requested")) {
          attention.push("changes_requested");
        }
        const body = (review.body ?? "").trim().replace(/\s+/g, " ").slice(0, 160);
        if (body) notes.push(`@${login} requested changes: ${body}`);
      } else if (review.state === "COMMENTED") {
        const submittedMs = Date.parse(review.submittedAt ?? "");
        if (
          myLastActivityMs > 0 &&
          !Number.isNaN(submittedMs) &&
          submittedMs <= myLastActivityMs
        ) {
          continue;
        }
        const body = (review.body ?? "").trim().replace(/\s+/g, " ").slice(0, 160);
        if (body) {
          attention.push("human_comment");
          notes.push(`@${login} reviewed: ${body}`);
        }
      }
    }

    for (const comment of meta.comments ?? []) {
      const login = comment.author?.login ?? "";
      if (!login || login.toLowerCase() === me || isBotLogin(login)) continue;
      const createdMs = Date.parse(comment.createdAt ?? "");
      if (
        myLastActivityMs > 0 &&
        !Number.isNaN(createdMs) &&
        createdMs <= myLastActivityMs
      ) {
        continue;
      }
      const rawBody = comment.body ?? "";
      if (isAutomationComment(rawBody)) continue;
      const body = rawBody.trim().replace(/\s+/g, " ").slice(0, 160);
      if (!body) continue;
      attention.push("human_comment");
      notes.push(`@${login}: ${body}`);
    }

    // Unresolved, non-outdated inline review threads from humans only.
    try {
      const [ownerName, repoName] = full.split("/", 2) as [string, string];
      const threadRaw = await runOk("gh", [
        "api",
        "graphql",
        "-f",
        `query=query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:40){nodes{isResolved isOutdated comments(first:1){nodes{author{login} body path}}}}}}}`,
        "-F",
        `owner=${ownerName}`,
        "-F",
        `name=${repoName}`,
        "-F",
        `number=${pr.number}`,
        "--jq",
        ".data.repository.pullRequest.reviewThreads.nodes // []",
      ]);
      const threads = JSON.parse(threadRaw) as Array<{
        isResolved?: boolean;
        isOutdated?: boolean;
        comments?: {
          nodes?: Array<{ author?: { login?: string }; body?: string; path?: string }>;
        };
      }>;
      for (const thread of threads) {
        if (thread.isResolved || thread.isOutdated) continue;
        const first = thread.comments?.nodes?.[0];
        const login = first?.author?.login ?? "";
        if (!login || login.toLowerCase() === me || isBotLogin(login)) continue;
        const body = (first?.body ?? "").trim().replace(/\s+/g, " ").slice(0, 140);
        if (!body) continue;
        attention.push("review_comment");
        notes.push(`@${login} on ${first?.path ?? "?"}: ${body}`);
      }
    } catch {
      // best-effort
    }

    const checks = meta.statusCheckRollup ?? [];
    // statusCheckRollup can retain older failed runs for the same check name.
    // Keep the last entry per name so a later green run clears a prior failure.
    const latestByName = new Map<string, (typeof checks)[number]>();
    for (const c of checks) {
      const name = (c.name ?? "").trim() || "check";
      latestByName.set(name, c);
    }
    const failing = [...latestByName.values()].filter((c) => {
      const state = (c.state ?? "").toUpperCase();
      const conclusion = (c.conclusion ?? "").toUpperCase();
      return (
        state === "FAILURE" ||
        state === "ERROR" ||
        conclusion === "FAILURE" ||
        conclusion === "TIMED_OUT" ||
        conclusion === "CANCELLED"
      );
    });
    const claFailing = failing.filter((c) => isClaCheckName(c.name ?? ""));
    const ciFailing = failing.filter((c) => !isClaCheckName(c.name ?? ""));
    if (ciFailing.length > 0) {
      attention.push("ci_failing");
      notes.push(
        `CI failing: ${ciFailing
          .slice(0, 3)
          .map((c) => c.name ?? "check")
          .join(", ")}`,
      );
    }
    if (claFailing.length > 0) {
      attention.push("cla_blocking");
      const claNote =
        full === "trinodb/trino"
          ? "CLA blocking: email signed PDF to cla@trino.io (see github.com/trinodb/cla); not cla-assistant.io"
          : `CLA blocking: ${claFailing
              .slice(0, 3)
              .map((c) => c.name ?? "check")
              .join(", ")}`;
      notes.push(claNote);
    }

    const item: CampaignMonitorItem = {
      repo: full,
      number: pr.number,
      title: meta.title ?? pr.title,
      url: meta.url ?? pr.url,
      reviewDecision: decision,
      mergeable: meta.mergeable ?? null,
      attention: [...new Set(attention)],
      notes: notes.slice(0, 8),
    };

    const actionable = item.attention.some(
      (a) =>
        a === "changes_requested" ||
        a === "human_comment" ||
        a === "review_comment" ||
        a === "ci_failing" ||
        a === "cla_blocking" ||
        a === "monitor_error",
    );
    if (item.attention.includes("approved") && !actionable) {
      approved.push(item);
    } else if (actionable) {
      needsAction.push(item);
      updateCandidate(
        `${owner}/${repo}#${pr.number}`,
        {
          notes: `monitor: ${item.attention.join(",")} | ${notes[0] ?? ""}`.slice(0, 500),
        },
        home,
      );
    } else {
      waiting += 1;
    }
  }

  logEvent(
    "campaign_monitor",
    auth.ghLogin,
    {
      checked,
      needsAction: needsAction.length,
      approved: approved.length,
      waiting,
    },
    home,
  );

  return { checked, needsAction, approved, waiting, merged, closed };
}

export function formatCampaignMonitor(result: CampaignMonitorResult): string {
  const lines = [
    `Monitored ${result.checked} campaign PR(s) still open: ${result.needsAction.length} need action, ${result.approved.length} approved, ${result.waiting} waiting.`,
  ];
  if ((result.merged ?? 0) > 0 || (result.closed ?? 0) > 0) {
    lines.push(
      `Settled this pass: ${result.merged ?? 0} merged, ${result.closed ?? 0} closed.`,
    );
  }
  if (result.needsAction.length === 0) {
    lines.push("No maintainer rework signals right now.");
  } else {
    lines.push("Needs action:");
    for (const item of result.needsAction) {
      lines.push(
        `  ${item.repo}#${item.number} [${item.attention.join(",")}] ${item.title}`,
      );
      lines.push(`    ${item.url}`);
      for (const note of item.notes.slice(0, 3)) {
        lines.push(`    - ${note}`);
      }
    }
  }
  if (result.approved.length > 0) {
    lines.push("Approved (awaiting merge):");
    for (const item of result.approved.slice(0, 10)) {
      lines.push(`  ${item.repo}#${item.number}  ${item.url}`);
    }
  }
  const learnWorthy = result.needsAction.some((item) =>
    item.attention.some(
      (a) =>
        a === "changes_requested" ||
        a === "human_comment" ||
        a === "review_comment" ||
        a === "ci_failing",
    ),
  );
  if (learnWorthy) {
    lines.push(
      "New learning? Append with: oocoder campaign learn --repo owner/name --source '#N' --title '…' --rule '…'",
    );
  }
  return lines.join("\n");
}

export interface CampaignLearningInput {
  /** owner/repo or topic slug (e.g. redis/redis, campaign-monitor). */
  repo: string;
  /** Short title for the log heading. */
  title: string;
  /** The durable rule / learning text. */
  rule: string;
  /** Provenance, e.g. #16227 or monitor/ci. */
  source?: string;
  /** ISO date (YYYY-MM-DD). Defaults to today UTC. */
  date?: string;
  /** When true, note that implement.ts / CAMPAIGN_LEARNINGS should be updated. */
  encode?: boolean;
  /** Optional override for the OoCoder checkout root. */
  projectRoot?: string;
  home?: string;
}

export interface CampaignLearningResult {
  logPath: string;
  date: string;
  entry: string;
}

/** Path to the append-only campaign rule log in the OoCoder checkout. */
export function campaignRuleLogPath(projectRoot = findProjectRoot()): string {
  return join(projectRoot, "docs", "CAMPAIGN_RULE_LOG.md");
}

const RULE_LOG_HEADER = `# Campaign rule log

Append-only record of new DE-campaign learnings. Newest days at the bottom.

After each entry:

1. Roll the rule into the topic section in \`docs/CAMPAIGN_LEARNINGS.md\`.
2. When it should steer future patches, encode it in \`packages/core/src/implement.ts\`.
3. Prefer \`oocoder campaign learn\` so the audit event is written too.

`;

function ensureRuleLog(logPath: string): void {
  mkdirSync(dirname(logPath), { recursive: true });
  if (!existsSync(logPath)) {
    writeFileSync(logPath, RULE_LOG_HEADER, "utf8");
  }
}

/** Format one rule-log entry (no file I/O). Exported for unit tests. */
export function formatCampaignLearningEntry(input: CampaignLearningInput): {
  date: string;
  entry: string;
} {
  const date = input.date ?? new Date().toISOString().slice(0, 10);
  const repo = input.repo.trim();
  const title = input.title.trim().replace(/\s+/g, " ");
  const rule = input.rule.trim().replace(/\s+/g, " ");
  if (!repo || !title || !rule) {
    throw new Error("campaign learn requires --repo, --title, and --rule");
  }
  const source = (input.source ?? "").trim();
  const lines = [
    `### ${repo} — ${title}`,
    source ? `- **Source:** ${source}` : null,
    `- **Rule:** ${rule}`,
    input.encode
      ? "- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`"
      : "- **Encode:** optional (rollup into CAMPAIGN_LEARNINGS when durable)",
    "",
  ].filter((line): line is string => line !== null);
  return { date, entry: lines.join("\n") };
}

/**
 * Append a new campaign learning to docs/CAMPAIGN_RULE_LOG.md and the audit DB.
 * Does not edit CAMPAIGN_LEARNINGS.md / implement.ts — those stay intentional.
 */
export function logCampaignLearning(
  input: CampaignLearningInput,
): CampaignLearningResult {
  const home = input.home ?? oocoderHome();
  const projectRoot = input.projectRoot ?? findProjectRoot();
  const logPath = campaignRuleLogPath(projectRoot);
  ensureRuleLog(logPath);
  const { date, entry } = formatCampaignLearningEntry(input);

  const existing = readFileSync(logPath, "utf8");
  const dayHeading = `## ${date}`;
  let next: string;
  if (existing.includes(`\n${dayHeading}\n`) || existing.startsWith(`${dayHeading}\n`)) {
    // Append under the existing day section (end of file is fine if days are ordered).
    const trimmed = existing.replace(/\s*$/, "\n");
    next = `${trimmed}\n${entry}`;
  } else {
    const trimmed = existing.replace(/\s*$/, "\n");
    next = `${trimmed}\n${dayHeading}\n\n${entry}`;
  }
  writeFileSync(logPath, next, "utf8");

  logEvent(
    "campaign_learning",
    "system",
    {
      repo: input.repo,
      title: input.title,
      source: input.source ?? null,
      encode: Boolean(input.encode),
      logPath,
      date,
    },
    home,
  );

  return { logPath, date, entry };
}

export function formatCampaignLearningResult(result: CampaignLearningResult): string {
  return [
    `Logged learning for ${result.date} → ${result.logPath}`,
    result.entry.trimEnd(),
    "Next: roll into docs/CAMPAIGN_LEARNINGS.md; encode in packages/core/src/implement.ts when it should steer patches.",
  ].join("\n");
}

export interface CampaignWatchOptions {
  /** Seconds between ticks (default 300). */
  intervalSec?: number;
  /** Max ticks; omit or 0 = run forever (systemd-friendly). */
  maxTicks?: number;
  /** When true, also open new PRs toward the campaign target each tick. */
  openPrs?: boolean;
  /** Max new PRs per tick when openPrs is on. */
  openLimit?: number;
  dryRun?: boolean;
  skipVerify?: boolean;
  runtime?: RuntimeMode;
  model?: string;
  home?: string;
  /** Injected sleep for tests. */
  sleep?: (ms: number) => Promise<void>;
  /** Called after each tick (tests / custom logging). */
  onTick?: (tick: CampaignWatchTickResult) => void | Promise<void>;
}

export interface CampaignWatchTickResult {
  tick: number;
  synced: number;
  scrubbed: number;
  monitor: CampaignMonitorResult;
  opened: number;
  ciFixes: number;
  messages: string[];
  status: CampaignStatus;
}

export interface CampaignWatchResult {
  ticks: number;
  last: CampaignWatchTickResult | null;
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** One monitor/sync/scrub/(optional run)/fixci cycle for the continuous watcher. */
export async function runCampaignWatchTick(
  opts: CampaignWatchOptions & { tick: number },
): Promise<CampaignWatchTickResult> {
  const home = opts.home ?? oocoderHome();
  const messages: string[] = [];
  const tick = opts.tick;

  let synced = 0;
  try {
    const sync = await syncCampaignPrs(home);
    synced = sync.synced;
    messages.push(`sync: ${synced} open PR(s)`);
  } catch (err) {
    messages.push(`sync failed: ${err instanceof Error ? err.message : String(err)}`);
  }

  let scrubbed = 0;
  try {
    const scrub = await scrubCampaignBranding(home);
    scrubbed = scrub.scrubbed;
    messages.push(`scrub: ${scrubbed}/${scrub.checked}`);
  } catch (err) {
    messages.push(`scrub failed: ${err instanceof Error ? err.message : String(err)}`);
  }

  let monitor: CampaignMonitorResult = {
    checked: 0,
    needsAction: [],
    approved: [],
    waiting: 0,
  };
  try {
    monitor = await monitorCampaignPrs(home);
    messages.push(
      `monitor: ${monitor.needsAction.length} action, ${monitor.approved.length} approved, ${monitor.waiting} waiting`,
    );
  } catch (err) {
    messages.push(
      `monitor failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }

  let opened = 0;
  if (opts.openPrs) {
    try {
      const runResult = await runCampaign({
        home,
        dryRun: opts.dryRun,
        skipDiscuss: true,
        skipVerify: opts.skipVerify ?? true,
        limit: opts.openLimit,
        runtime: opts.runtime ?? "local",
        model: opts.model,
      });
      opened = runResult.opened;
      messages.push(...runResult.messages.slice(-4));
    } catch (err) {
      messages.push(
        `run failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  let ciFixes = 0;
  try {
    const auth = await checkAuth();
    if (auth.ghLogin && loadPolicy(home).ciFix.enabled) {
      const fixes = await followUpCiFixes({
        login: auth.ghLogin,
        dryRun: opts.dryRun,
      });
      ciFixes = fixes.filter((f) => f.ok).length;
      messages.push(`fixci: ${ciFixes}/${fixes.length} ok`);
    }
  } catch (err) {
    messages.push(
      `fixci failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }

  const status = getCampaignStatus(home);
  logEvent(
    "campaign_watch_tick",
    null,
    {
      tick,
      synced,
      scrubbed,
      opened,
      ciFixes,
      needsAction: monitor.needsAction.length,
    },
    home,
  );

  return {
    tick,
    synced,
    scrubbed,
    monitor,
    opened,
    ciFixes,
    messages,
    status,
  };
}

/**
 * Continuous campaign watcher for Linux systemd / long-running agents.
 * Default: forever loop with 300s interval (use --max-ticks for one-shot).
 */
export async function runCampaignWatch(
  opts: CampaignWatchOptions = {},
): Promise<CampaignWatchResult> {
  const intervalSec = Math.max(30, opts.intervalSec ?? 300);
  const maxTicks = opts.maxTicks && opts.maxTicks > 0 ? opts.maxTicks : 0;
  const sleep = opts.sleep ?? defaultSleep;
  let ticks = 0;
  let last: CampaignWatchTickResult | null = null;

  for (;;) {
    ticks += 1;
    last = await runCampaignWatchTick({ ...opts, tick: ticks });
    if (opts.onTick) await opts.onTick(last);
    if (maxTicks > 0 && ticks >= maxTicks) break;
    await sleep(intervalSec * 1000);
  }

  return { ticks, last };
}

export function formatCampaignWatchTick(tick: CampaignWatchTickResult): string {
  const lines = [
    `=== campaign watch tick ${tick.tick} ===`,
    ...tick.messages,
    formatCampaignMonitor(tick.monitor),
    formatCampaignStatus(tick.status),
  ];
  return lines.join("\n");
}
