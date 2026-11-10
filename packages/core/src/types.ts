export type RuntimeMode = "local" | "cloud";

export interface AllowlistConfig {
  orgs: string[];
  repos: string[];
  strict: boolean;
}

export interface CiFixConfig {
  enabled: boolean;
  maxAttemptsPerPr: number;
  maxFixesPerRun: number;
  minMinutesAfterPush: number;
  maxFilesTouched: number;
  maxLogBytes: number;
}

export interface TelemetryConfig {
  enabled: boolean;
}

export interface DashboardConfig {
  refreshPrStates: boolean;
  historyDays: number;
}

export type CiOutcome =
  | "patched"
  | "tests_failed"
  | "no_fix"
  | "gave_up"
  | "dry_run";

export interface PolicyConfig {
  labels: string[];
  quality: {
    maxFilesChanged: number;
    maxLinesChanged: number;
  };
  rateLimits: {
    maxIssuesPerDay: number;
    maxPrsPerDay: number;
    maxCommentsPerDay: number;
  };
  budget: {
    maxUsdPerRun: number;
  };
  disclosure: string;
  prFooter: string;
  ciFix: CiFixConfig;
  telemetry: TelemetryConfig;
  dashboard: DashboardConfig;
}

export interface IssueRef {
  owner: string;
  repo: string;
  number: number;
  url: string;
  title: string;
  body: string;
  labels: string[];
  comments: number;
  createdAt: string;
  updatedAt: string;
  score: number;
}

export interface CandidateRecord {
  id: string;
  owner: string;
  repo: string;
  number: number;
  url: string;
  title: string;
  labels: string;
  score: number;
  status: "discovered" | "discussed" | "working" | "pr_opened" | "failed" | "skipped";
  discoveredAt: string;
  updatedAt: string;
  notes: string | null;
  prUrl: string | null;
}

export interface WorkOptions {
  issueUrl: string;
  dryRun?: boolean;
  skipPr?: boolean;
  skipDiscuss?: boolean;
  runtime?: RuntimeMode;
  model?: string;
  maxBudgetUsd?: number;
  workRoot?: string;
}

export interface WorkResult {
  issue: IssueRef;
  branch: string;
  worktreePath: string | null;
  prUrl: string | null;
  dryRun: boolean;
  skippedPr: boolean;
  filesChanged: number;
  linesChanged: number;
  agentId: string | null;
  message: string;
}

export interface DiscoverOptions {
  labels?: string[];
  language?: string;
  limit?: number;
  queryExtra?: string;
}

export interface QualityDiffStats {
  filesChanged: number;
  linesChanged: number;
  ok: boolean;
  reason?: string;
}

export interface AuthStatus {
  gh: boolean;
  ghLogin: string | null;
  cursorApiKey: boolean;
  grokApiKey: boolean;
  git: boolean;
  message: string;
}
