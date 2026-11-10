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

  const found = await searchIssues({
    labels,
    language: options.language,
    limit: Math.max(limit * 3, 30),
    queryExtra: options.queryExtra,
  });

  const filtered = found
    .filter((issue) => {
      if (allowlist.repos.length === 0 && allowlist.orgs.length === 0) {
        // Still discover globally when allowlist empty, but mark for review.
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
    },
    home,
  );

  return filtered;
}
