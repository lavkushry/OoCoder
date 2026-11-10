import { loadAllowlist, loadPolicy, isAllowed } from "./config.js";
import { searchIssues } from "./github.js";
import { logEvent, upsertCandidate } from "./state.js";
import { noteObservation } from "./telemetry.js";
import type { DiscoverOptions, IssueRef } from "./types.js";
import { oocoderHome } from "./paths.js";

export async function discoverIssues(
  options: DiscoverOptions = {},
  home = oocoderHome(),
): Promise<IssueRef[]> {
  const policy = loadPolicy(home);
  const allowlist = loadAllowlist(home);
  const labels = options.labels?.length ? options.labels : policy.labels;
  const limit = options.limit ?? 20;

  // When an allowlist is set, search each repo directly. Global label search
  // almost never surfaces our DE targets (Airflow/Kafka/Spark/…).
  const repoTargets =
    options.queryExtra || (allowlist.repos.length === 0 && allowlist.orgs.length === 0)
      ? [undefined]
      : allowlist.repos;

  const byKey = new Map<string, IssueRef>();
  const perRepoLimit = Math.max(5, Math.ceil((limit * 2) / Math.max(repoTargets.length, 1)));

  for (const repo of repoTargets) {
    const found = await searchIssues({
      labels,
      language: options.language,
      limit: perRepoLimit,
      queryExtra: options.queryExtra,
      ...(repo ? { repo } : {}),
    });
    for (const issue of found) {
      const key = `${issue.owner}/${issue.repo}#${issue.number}`;
      const prev = byKey.get(key);
      if (!prev || issue.score > prev.score) byKey.set(key, issue);
    }
  }

  const filtered = [...byKey.values()]
    .filter((issue) => {
      if (allowlist.repos.length === 0 && allowlist.orgs.length === 0) {
        return true;
      }
      return isAllowed(issue.owner, issue.repo, allowlist);
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);

  for (const issue of filtered) {
    upsertCandidate(issue, home);
  }

  const byRepo = new Map<string, number>();
  for (const issue of filtered) {
    const key = `${issue.owner}/${issue.repo}`;
    byRepo.set(key, (byRepo.get(key) ?? 0) + 1);
  }
  for (const [repo, count] of byRepo) {
    noteObservation(repo, "issue", count, home);
  }
  if (filtered.length === 0) {
    noteObservation("*", "skip:empty", 1, home);
  }

  logEvent(
    "discover",
    null,
    {
      count: filtered.length,
      labels,
      language: options.language ?? null,
      reposSearched: repoTargets.filter(Boolean).length,
    },
    home,
  );

  return filtered;
}
