import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import yaml from "js-yaml";
import { z } from "zod";
import type { AllowlistConfig, PolicyConfig } from "./types.js";
import { oocoderHome, paths, projectConfigPaths } from "./paths.js";

const AllowlistSchema = z.object({
  orgs: z.array(z.string()).nullable().default([]).transform((v) => v ?? []),
  repos: z.array(z.string()).nullable().default([]).transform((v) => v ?? []),
  strict: z.boolean().default(true),
});

const PolicySchema = z.object({
  labels: z.array(z.string()).default([
    "good first issue",
    "help wanted",
    "documentation",
    "bug",
  ]),
  quality: z
    .object({
      maxFilesChanged: z.number().default(20),
      maxLinesChanged: z.number().default(400),
    })
    .default({ maxFilesChanged: 20, maxLinesChanged: 400 }),
  rateLimits: z
    .object({
      maxIssuesPerDay: z.number().default(5),
      maxPrsPerDay: z.number().default(3),
      maxCommentsPerDay: z.number().default(10),
    })
    .default({
      maxIssuesPerDay: 5,
      maxPrsPerDay: 3,
      maxCommentsPerDay: 10,
    }),
  budget: z
    .object({
      maxUsdPerRun: z.number().default(5),
    })
    .default({ maxUsdPerRun: 5 }),
  disclosure: z.string().default(
    "---\n*Posted by OoCoder, an AI coding agent. A human maintains the allowlist and policy.*",
  ),
  prFooter: z.string().default(""),
  ciFix: z
    .object({
      enabled: z.boolean().default(true),
      maxAttemptsPerPr: z.number().default(2),
      maxFixesPerRun: z.number().default(1),
      minMinutesAfterPush: z.number().default(10),
      maxFilesTouched: z.number().default(3),
      maxLogBytes: z.number().default(60000),
    })
    .default({
      enabled: true,
      maxAttemptsPerPr: 2,
      maxFixesPerRun: 1,
      minMinutesAfterPush: 10,
      maxFilesTouched: 3,
      maxLogBytes: 60000,
    }),
  telemetry: z
    .object({
      enabled: z.boolean().default(true),
    })
    .default({ enabled: true }),
  dashboard: z
    .object({
      refreshPrStates: z.boolean().default(true),
      historyDays: z.number().default(30),
    })
    .default({ refreshPrStates: true, historyDays: 30 }),
});

function loadYamlFile(filePath: string): unknown {
  if (!existsSync(filePath)) {
    throw new Error(`Config not found: ${filePath}`);
  }
  return yaml.load(readFileSync(filePath, "utf8"));
}

export function loadAllowlist(home = oocoderHome()): AllowlistConfig {
  const p = paths(home);
  const project = projectConfigPaths();
  const file = existsSync(p.allowlist) ? p.allowlist : project.allowlist;
  return AllowlistSchema.parse(loadYamlFile(file) ?? {});
}

export function loadPolicy(home = oocoderHome()): PolicyConfig {
  const p = paths(home);
  const project = projectConfigPaths();
  const file = existsSync(p.policy) ? p.policy : project.policy;
  return PolicySchema.parse(loadYamlFile(file) ?? {});
}

export function isAllowed(
  owner: string,
  repo: string,
  allowlist: AllowlistConfig = loadAllowlist(),
): boolean {
  const full = `${owner}/${repo}`.toLowerCase();
  if (allowlist.repos.some((r) => r.toLowerCase() === full)) {
    return true;
  }
  if (allowlist.orgs.some((o) => o.toLowerCase() === owner.toLowerCase())) {
    return true;
  }
  if (!allowlist.strict && allowlist.repos.length === 0 && allowlist.orgs.length === 0) {
    return true;
  }
  return false;
}

/** Daemon/work selection: keep only issues that pass the allowlist gate. */
export function filterAllowlistedIssues<T extends { owner: string; repo: string }>(
  issues: T[],
  allowlist: AllowlistConfig = loadAllowlist(),
): T[] {
  return issues.filter((issue) => isAllowed(issue.owner, issue.repo, allowlist));
}

export function ensureHomeInitialized(home = oocoderHome()): {
  home: string;
  created: boolean;
} {
  const p = paths(home);
  const project = projectConfigPaths();
  let created = false;

  if (!existsSync(p.home)) {
    mkdirSync(p.home, { recursive: true });
    created = true;
  }
  mkdirSync(p.work, { recursive: true });
  mkdirSync(p.logs, { recursive: true });

  if (!existsSync(p.allowlist)) {
    if (existsSync(project.allowlist)) {
      copyFileSync(project.allowlist, p.allowlist);
    } else {
      writeFileSync(
        p.allowlist,
        yaml.dump({ orgs: [], repos: [], strict: true }),
        "utf8",
      );
    }
    created = true;
  }

  if (!existsSync(p.policy)) {
    if (existsSync(project.policy)) {
      copyFileSync(project.policy, p.policy);
    } else {
      writeFileSync(p.policy, yaml.dump(PolicySchema.parse({})), "utf8");
    }
    created = true;
  }

  return { home: p.home, created };
}
