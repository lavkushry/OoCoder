import { grokApiKey } from "./grok.js";
import { run, runOk } from "./shell.js";
import type { AuthStatus, IssueRef } from "./types.js";

const ISSUE_URL_RE =
  /^https?:\/\/github\.com\/([^/]+)\/([^/]+)\/issues\/(\d+)\/?(?:[?#].*)?$/i;

export function parseIssueUrl(input: string): {
  owner: string;
  repo: string;
  number: number;
  url: string;
} {
  const trimmed = input.trim();
  const match = ISSUE_URL_RE.exec(trimmed);
  if (match) {
    const owner = match[1]!;
    const repo = match[2]!;
    const number = Number(match[3]);
    return {
      owner,
      repo,
      number,
      url: `https://github.com/${owner}/${repo}/issues/${number}`,
    };
  }

  const short = /^([^/\s]+)\/([^#\s]+)#(\d+)$/.exec(trimmed);
  if (short) {
    const owner = short[1]!;
    const repo = short[2]!;
    const number = Number(short[3]);
    return {
      owner,
      repo,
      number,
      url: `https://github.com/${owner}/${repo}/issues/${number}`,
    };
  }

  throw new Error(
    `Invalid issue reference: ${input}. Use https://github.com/owner/repo/issues/123 or owner/repo#123`,
  );
}

export async function checkAuth(): Promise<AuthStatus> {
  const git = (await run("which", ["git"])).code === 0;
  const ghWhich = (await run("which", ["gh"])).code === 0;
  let gh = false;
  let ghLogin: string | null = null;

  if (ghWhich) {
    const status = await run("gh", ["auth", "status"]);
    gh = status.code === 0;
    if (gh) {
      try {
        ghLogin = await runOk("gh", ["api", "user", "--jq", ".login"]);
      } catch {
        ghLogin = null;
      }
    }
  }

  const cursorApiKey = hasUsableCursorAuth();
  const hasGrokKey = Boolean(grokApiKey());

  const parts: string[] = [];
  if (!git) parts.push("git missing");
  if (!ghWhich) parts.push("gh missing");
  else if (!gh) parts.push("gh not authenticated (run: gh auth login)");
  if (!cursorApiKey && !hasGrokKey) {
    parts.push("Set CURSOR_API_KEY or GROK_API_KEY before a live coding run");
  }

  return {
    gh,
    ghLogin,
    cursorApiKey,
    grokApiKey: hasGrokKey,
    git,
    message: parts.length === 0 ? "ok" : parts.join("; "),
  };
}

/** True when a real Cursor credential is present (rejects placeholders like "…"). */
export function hasUsableCursorAuth(): boolean {
  const file = process.env.CURSOR_API_KEY_FILE?.trim();
  if (file) return true;
  const sa = process.env.CURSOR_SERVICE_ACCOUNT_KEY?.trim();
  if (sa && !isPlaceholderSecret(sa)) return true;
  const key = process.env.CURSOR_API_KEY?.trim();
  return Boolean(key && !isPlaceholderSecret(key));
}

export function isPlaceholderSecret(value: string): boolean {
  const v = value.trim();
  if (!v) return true;
  // Common copy-paste placeholders from docs / shells.
  if (v === "…" || v === "..." || v === "YOUR_KEY" || v === "your-key") return true;
  if (/^(x+|changeme|replace.?me|todo)$/i.test(v)) return true;
  return false;
}

interface GhIssueJson {
  number: number;
  title: string;
  body: string | null;
  url: string;
  createdAt: string;
  updatedAt: string;
  commentsCount?: number;
  comments?: number;
  labels?: Array<string | { name: string }>;
  repository?: { nameWithOwner?: string };
}

function labelsOf(issue: GhIssueJson): string[] {
  if (!issue.labels) return [];
  return issue.labels.map((l) => (typeof l === "string" ? l : l.name));
}

function scoreIssue(issue: GhIssueJson, preferredLabels: string[]): number {
  const labels = labelsOf(issue).map((l) => l.toLowerCase());
  let score = 0;
  for (const pref of preferredLabels) {
    if (labels.includes(pref.toLowerCase())) score += 10;
  }
  if (labels.includes("good first issue")) score += 5;
  if (labels.includes("documentation")) score += 2;
  const comments = issue.commentsCount ?? issue.comments ?? 0;
  score += Math.max(0, 5 - comments);
  const ageDays =
    (Date.now() - new Date(issue.updatedAt).getTime()) / (1000 * 60 * 60 * 24);
  if (ageDays < 30) score += 3;
  else if (ageDays < 90) score += 1;
  return score;
}

export async function fetchIssue(issueUrl: string): Promise<IssueRef> {
  const ref = parseIssueUrl(issueUrl);
  const raw = await runOk("gh", [
    "api",
    `repos/${ref.owner}/${ref.repo}/issues/${ref.number}`,
  ]);
  const data = JSON.parse(raw) as {
    number: number;
    title: string;
    body: string | null;
    html_url: string;
    created_at: string;
    updated_at: string;
    comments: number;
    labels: Array<{ name: string }>;
  };
  return {
    owner: ref.owner,
    repo: ref.repo,
    number: data.number,
    url: data.html_url,
    title: data.title,
    body: data.body ?? "",
    labels: data.labels.map((l) => l.name),
    comments: data.comments,
    createdAt: data.created_at,
    updatedAt: data.updated_at,
    score: 0,
  };
}

export async function searchIssues(options: {
  labels: string[];
  language?: string;
  limit: number;
  queryExtra?: string;
}): Promise<IssueRef[]> {
  // Prefer gh flags over a single query string — `label:"..."` in a free
  // query often returns empty on newer gh versions; --label is reliable.
  const byLabel = new Map<string, GhIssueJson>();

  for (const label of options.labels.length ? options.labels : ["good first issue"]) {
    const args = [
      "search",
      "issues",
      "--state",
      "open",
      "--limit",
      String(options.limit),
      "--label",
      label,
      "--json",
      "number,title,body,url,createdAt,updatedAt,labels,commentsCount,repository",
    ];
    if (options.language) {
      args.push("--language", options.language);
    }
    if (options.queryExtra) {
      args.push(options.queryExtra);
    }

    const raw = await runOk("gh", args);
    const items = JSON.parse(raw) as GhIssueJson[];
    for (const item of items) {
      const key = `${item.repository?.nameWithOwner ?? item.url}#${item.number}`;
      if (!byLabel.has(key)) byLabel.set(key, item);
    }
  }

  return [...byLabel.values()].map((item) => {
    const full =
      item.repository?.nameWithOwner ??
      (() => {
        const m = /github\.com\/([^/]+)\/([^/]+)\/issues\//.exec(item.url);
        return m ? `${m[1]}/${m[2]}` : "unknown/unknown";
      })();
    const [owner, repo] = full.split("/");
    return {
      owner: owner!,
      repo: repo!,
      number: item.number,
      url: item.url,
      title: item.title,
      body: item.body ?? "",
      labels: labelsOf(item),
      comments: item.commentsCount ?? item.comments ?? 0,
      createdAt: item.createdAt,
      updatedAt: item.updatedAt,
      score: scoreIssue(item, options.labels),
    };
  });
}

export async function postIssueComment(
  owner: string,
  repo: string,
  number: number,
  body: string,
): Promise<string> {
  const raw = await runOk("gh", [
    "api",
    `repos/${owner}/${repo}/issues/${number}/comments`,
    "-f",
    `body=${body}`,
  ]);
  const data = JSON.parse(raw) as { html_url: string };
  return data.html_url;
}

export async function createPullRequest(options: {
  cwd: string;
  title: string;
  body: string;
  head: string;
  base?: string;
  repo?: string;
}): Promise<string> {
  const args = [
    "pr",
    "create",
    "--title",
    options.title,
    "--body",
    options.body,
    "--head",
    options.head,
  ];
  if (options.base) {
    args.push("--base", options.base);
  }
  if (options.repo) {
    args.push("--repo", options.repo);
  }
  return runOk("gh", args, { cwd: options.cwd });
}

export async function defaultBranch(
  owner: string,
  repo: string,
): Promise<string> {
  return runOk("gh", [
    "api",
    `repos/${owner}/${repo}`,
    "--jq",
    ".default_branch",
  ]);
}

export async function hasPushAccess(owner: string, repo: string): Promise<boolean> {
  try {
    const perm = await runOk("gh", [
      "api",
      `repos/${owner}/${repo}`,
      "--jq",
      ".permissions.push",
    ]);
    return perm === "true";
  } catch {
    return false;
  }
}

/** Args for `gh repo fork <upstream>` when not already inside a local checkout.
 * Do not pass `--remote` / `--remote=false` — gh rejects those with a repo arg. */
export function forkRepoArgs(upstream: string): string[] {
  return ["repo", "fork", upstream, "--default-branch-only"];
}

async function repoExists(owner: string, repo: string): Promise<boolean> {
  const result = await run("gh", [
    "api",
    `repos/${owner}/${repo}`,
    "--jq",
    ".full_name",
  ]);
  return result.code === 0;
}

async function waitForRepo(
  owner: string,
  repo: string,
  attempts = 15,
  delayMs = 1000,
): Promise<void> {
  for (let i = 0; i < attempts; i++) {
    if (await repoExists(owner, repo)) return;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  throw new Error(
    `Fork ${owner}/${repo} was created but is not visible yet; retry in a moment`,
  );
}

export async function ensureFork(
  owner: string,
  repo: string,
  login: string,
): Promise<{ forkOwner: string; cloneUrl: string; upstream: string }> {
  const upstream = `${owner}/${repo}`;
  if (await hasPushAccess(owner, repo)) {
    return {
      forkOwner: owner,
      cloneUrl: `https://github.com/${owner}/${repo}.git`,
      upstream,
    };
  }

  if (!(await repoExists(login, repo))) {
    // Create fork (idempotent). Must not use --remote when a repo arg is set.
    await runOk("gh", forkRepoArgs(upstream));
    await waitForRepo(login, repo);
  }

  return {
    forkOwner: login,
    cloneUrl: `https://github.com/${login}/${repo}.git`,
    upstream,
  };
}

export function parsePullUrl(input: string): {
  owner: string;
  repo: string;
  number: number;
} {
  const url = /github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/.exec(input.trim());
  if (url) {
    return { owner: url[1]!, repo: url[2]!, number: Number(url[3]) };
  }
  const short = /^([^/\s]+)\/([^#\s]+)#(\d+)$/.exec(input.trim());
  if (short) {
    return { owner: short[1]!, repo: short[2]!, number: Number(short[3]) };
  }
  throw new Error(`Invalid pull reference: ${input}`);
}

async function ghJson(path: string): Promise<unknown> {
  const raw = await runOk("gh", ["api", path]);
  return JSON.parse(raw) as unknown;
}

export async function getPull(
  owner: string,
  repo: string,
  number: number,
): Promise<Record<string, unknown>> {
  return (await ghJson(`repos/${owner}/${repo}/pulls/${number}`)) as Record<
    string,
    unknown
  >;
}

export async function checkRuns(
  owner: string,
  repo: string,
  sha: string,
): Promise<Array<Record<string, unknown>>> {
  const data = (await ghJson(
    `repos/${owner}/${repo}/commits/${sha}/check-runs`,
  )) as { check_runs?: Array<Record<string, unknown>> };
  return data.check_runs ?? [];
}

export async function combinedStatus(
  owner: string,
  repo: string,
  sha: string,
): Promise<{ state: string; statuses: unknown[] }> {
  const data = (await ghJson(`repos/${owner}/${repo}/commits/${sha}/status`)) as {
    state?: string;
    statuses?: unknown[];
  };
  return { state: data.state ?? "pending", statuses: data.statuses ?? [] };
}

export async function checkRunAnnotations(
  owner: string,
  repo: string,
  checkRunId: number,
): Promise<Array<Record<string, unknown>>> {
  const data = await ghJson(
    `repos/${owner}/${repo}/check-runs/${checkRunId}/annotations`,
  );
  return Array.isArray(data) ? (data as Array<Record<string, unknown>>) : [];
}

export async function actionsRunsForSha(
  owner: string,
  repo: string,
  sha: string,
): Promise<Array<Record<string, unknown>>> {
  try {
    const data = (await ghJson(
      `repos/${owner}/${repo}/actions/runs?head_sha=${sha}`,
    )) as { workflow_runs?: Array<Record<string, unknown>> };
    return data.workflow_runs ?? [];
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (message.includes("403")) {
      console.error(
        "ci logs unavailable (token lacks Actions:Read) — using annotations only",
      );
      return [];
    }
    throw err;
  }
}

export async function jobLogs(
  owner: string,
  repo: string,
  jobId: number,
  maxBytes = 60000,
): Promise<string> {
  try {
    const raw = await runOk("gh", [
      "api",
      `repos/${owner}/${repo}/actions/jobs/${jobId}/logs`,
    ]);
    if (raw.length <= maxBytes) return raw;
    return raw.slice(-maxBytes);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (message.includes("403")) {
      console.error(
        "ci logs unavailable (token lacks Actions:Read) — using annotations only",
      );
      return "";
    }
    return "";
  }
}

export function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}
