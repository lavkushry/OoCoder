import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type { CandidateRecord, IssueRef } from "./types.js";
import { oocoderHome, paths } from "./paths.js";

export type CandidateStatus = CandidateRecord["status"];

let dbInstance: DatabaseSync | null = null;
let dbPath: string | null = null;

export function getDb(home = oocoderHome()): DatabaseSync {
  const p = paths(home);
  if (dbInstance && dbPath === p.db) return dbInstance;
  if (dbInstance) {
    dbInstance.close();
    dbInstance = null;
  }
  mkdirSync(dirname(p.db), { recursive: true });
  const db = new DatabaseSync(p.db);
  db.exec(`
    CREATE TABLE IF NOT EXISTS candidates (
      id TEXT PRIMARY KEY,
      owner TEXT NOT NULL,
      repo TEXT NOT NULL,
      number INTEGER NOT NULL,
      url TEXT NOT NULL,
      title TEXT NOT NULL,
      labels TEXT NOT NULL DEFAULT '[]',
      score REAL NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'discovered',
      discovered_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      notes TEXT,
      pr_url TEXT
    );

    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      kind TEXT NOT NULL,
      ref TEXT,
      payload TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS rate_counters (
      day TEXT NOT NULL,
      kind TEXT NOT NULL,
      count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (day, kind)
    );

    CREATE TABLE IF NOT EXISTS ci_fix_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts REAL NOT NULL,
      repo TEXT NOT NULL,
      pr_number INTEGER NOT NULL,
      head_sha TEXT NOT NULL,
      outcome TEXT NOT NULL,
      detail TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_ci_pr ON ci_fix_attempts (repo, pr_number);

    CREATE TABLE IF NOT EXISTS observations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts REAL NOT NULL,
      repo TEXT NOT NULL,
      kind TEXT NOT NULL,
      candidates INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS usage (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts REAL NOT NULL,
      prompt_tokens INTEGER NOT NULL,
      completion_tokens INTEGER NOT NULL
    );
  `);
  dbInstance = db;
  dbPath = p.db;
  return db;
}

export function closeDb(): void {
  if (dbInstance) {
    dbInstance.close();
    dbInstance = null;
    dbPath = null;
  }
}

function candidateId(owner: string, repo: string, number: number): string {
  return `${owner}/${repo}#${number}`;
}

function rowToCandidate(row: Record<string, unknown>): CandidateRecord {
  return {
    id: String(row.id),
    owner: String(row.owner),
    repo: String(row.repo),
    number: Number(row.number),
    url: String(row.url),
    title: String(row.title),
    labels: String(row.labels),
    score: Number(row.score),
    status: row.status as CandidateStatus,
    discoveredAt: String(row.discovered_at),
    updatedAt: String(row.updated_at),
    notes: row.notes == null ? null : String(row.notes),
    prUrl: row.pr_url == null ? null : String(row.pr_url),
  };
}

export function upsertCandidate(issue: IssueRef, home = oocoderHome()): CandidateRecord {
  const db = getDb(home);
  const now = new Date().toISOString();
  const id = candidateId(issue.owner, issue.repo, issue.number);
  db.prepare(
    `
    INSERT INTO candidates (id, owner, repo, number, url, title, labels, score, status, discovered_at, updated_at, notes, pr_url)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?, NULL, NULL)
    ON CONFLICT(id) DO UPDATE SET
      title = excluded.title,
      labels = excluded.labels,
      score = excluded.score,
      url = excluded.url,
      updated_at = excluded.updated_at
  `,
  ).run(
    id,
    issue.owner,
    issue.repo,
    issue.number,
    issue.url,
    issue.title,
    JSON.stringify(issue.labels),
    issue.score,
    now,
    now,
  );
  const row = db.prepare(`SELECT * FROM candidates WHERE id = ?`).get(id) as Record<
    string,
    unknown
  >;
  return rowToCandidate(row);
}

export function updateCandidate(
  id: string,
  patch: Partial<{
    status: CandidateStatus;
    notes: string | null;
    prUrl: string | null;
  }>,
  home = oocoderHome(),
): void {
  const db = getDb(home);
  const existing = db.prepare(`SELECT * FROM candidates WHERE id = ?`).get(id) as
    | Record<string, unknown>
    | undefined;
  if (!existing) return;
  const now = new Date().toISOString();
  db.prepare(
    `
    UPDATE candidates
    SET status = ?,
        notes = ?,
        pr_url = ?,
        updated_at = ?
    WHERE id = ?
  `,
  ).run(
    String(patch.status ?? existing.status),
    patch.notes !== undefined
      ? patch.notes
      : existing.notes == null
        ? null
        : String(existing.notes),
    patch.prUrl !== undefined
      ? patch.prUrl
      : existing.pr_url == null
        ? null
        : String(existing.pr_url),
    now,
    id,
  );
}

export function listCandidates(
  status?: CandidateStatus,
  home = oocoderHome(),
): CandidateRecord[] {
  const db = getDb(home);
  const rows = status
    ? (db
        .prepare(
          `SELECT * FROM candidates WHERE status = ? ORDER BY score DESC, updated_at DESC`,
        )
        .all(status) as Record<string, unknown>[])
    : (db
        .prepare(`SELECT * FROM candidates ORDER BY score DESC, updated_at DESC`)
        .all() as Record<string, unknown>[]);
  return rows.map(rowToCandidate);
}

export function logEvent(
  kind: string,
  ref: string | null,
  payload: unknown,
  home = oocoderHome(),
): void {
  const db = getDb(home);
  db.prepare(
    `INSERT INTO events (kind, ref, payload, created_at) VALUES (?, ?, ?, ?)`,
  ).run(kind, ref, JSON.stringify(payload), new Date().toISOString());
}

function todayKey(): string {
  return new Date().toISOString().slice(0, 10);
}

export function getRateCount(
  kind: "issues" | "prs" | "comments",
  home = oocoderHome(),
): number {
  const db = getDb(home);
  const row = db
    .prepare(`SELECT count FROM rate_counters WHERE day = ? AND kind = ?`)
    .get(todayKey(), kind) as { count: number } | undefined;
  return row?.count ?? 0;
}

export function bumpRate(
  kind: "issues" | "prs" | "comments",
  home = oocoderHome(),
): number {
  const db = getDb(home);
  const day = todayKey();
  db.prepare(
    `
    INSERT INTO rate_counters (day, kind, count) VALUES (?, ?, 1)
    ON CONFLICT(day, kind) DO UPDATE SET count = count + 1
  `,
  ).run(day, kind);
  return getRateCount(kind, home);
}

export type CiOutcomeName =
  | "patched"
  | "tests_failed"
  | "no_fix"
  | "gave_up"
  | "dry_run";

export function recordCiAttempt(
  repo: string,
  prNumber: number,
  headSha: string,
  outcome: CiOutcomeName,
  detail = "",
  home = oocoderHome(),
): void {
  const db = getDb(home);
  db.prepare(
    `INSERT INTO ci_fix_attempts (ts, repo, pr_number, head_sha, outcome, detail)
     VALUES (?, ?, ?, ?, ?, ?)`,
  ).run(Date.now() / 1000, repo, prNumber, headSha, outcome, detail);
}

export function ciAttemptCount(
  repo: string,
  prNumber: number,
  home = oocoderHome(),
): number {
  const db = getDb(home);
  const row = db
    .prepare(
      `SELECT COUNT(*) AS n FROM ci_fix_attempts WHERE repo = ? AND pr_number = ? AND outcome != 'dry_run'`,
    )
    .get(repo, prNumber) as { n: number };
  return Number(row.n);
}

export function ciAttemptedSha(
  repo: string,
  prNumber: number,
  headSha: string,
  home = oocoderHome(),
): boolean {
  const db = getDb(home);
  const row = db
    .prepare(
      `SELECT 1 AS ok FROM ci_fix_attempts WHERE repo = ? AND pr_number = ? AND head_sha = ? LIMIT 1`,
    )
    .get(repo, prNumber, headSha) as { ok: number } | undefined;
  return Boolean(row);
}

export function ciHasOutcome(
  repo: string,
  prNumber: number,
  outcome: CiOutcomeName,
  home = oocoderHome(),
): boolean {
  const db = getDb(home);
  const row = db
    .prepare(
      `SELECT 1 AS ok FROM ci_fix_attempts WHERE repo = ? AND pr_number = ? AND outcome = ? LIMIT 1`,
    )
    .get(repo, prNumber, outcome) as { ok: number } | undefined;
  return Boolean(row);
}

export function listCiAttempts(home = oocoderHome()): Array<{
  repo: string;
  prNumber: number;
  headSha: string;
  outcome: string;
  detail: string;
  ts: number;
}> {
  const db = getDb(home);
  const rows = db
    .prepare(
      `SELECT repo, pr_number, head_sha, outcome, detail, ts FROM ci_fix_attempts ORDER BY ts DESC`,
    )
    .all() as Array<Record<string, unknown>>;
  return rows.map((row) => ({
    repo: String(row.repo),
    prNumber: Number(row.pr_number),
    headSha: String(row.head_sha),
    outcome: String(row.outcome),
    detail: String(row.detail ?? ""),
    ts: Number(row.ts),
  }));
}

export function recordObservation(
  repo: string,
  kind: string,
  candidates: number,
  home = oocoderHome(),
): void {
  const db = getDb(home);
  db.prepare(
    `INSERT INTO observations (ts, repo, kind, candidates) VALUES (?, ?, ?, ?)`,
  ).run(Date.now() / 1000, repo, kind, candidates);
}

export function recordUsage(
  promptTokens: number,
  completionTokens: number,
  home = oocoderHome(),
): void {
  const db = getDb(home);
  db.prepare(
    `INSERT INTO usage (ts, prompt_tokens, completion_tokens) VALUES (?, ?, ?)`,
  ).run(Date.now() / 1000, promptTokens, completionTokens);
}

export function assertRateLimit(
  kind: "issues" | "prs" | "comments",
  max: number,
  home = oocoderHome(),
): void {
  const count = getRateCount(kind, home);
  if (count >= max) {
    throw new Error(
      `Rate limit reached for ${kind}: ${count}/${max} today. Adjust configs/policy.yaml or wait until tomorrow.`,
    );
  }
}
