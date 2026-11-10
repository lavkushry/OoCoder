import { isAllowed, loadAllowlist, loadPolicy } from "./config.js";
import { fetchIssue, postIssueComment } from "./github.js";
import {
  assertRateLimit,
  bumpRate,
  logEvent,
  updateCandidate,
} from "./state.js";
import { oocoderHome } from "./paths.js";

export async function discussIssue(
  issueUrl: string,
  options: { dryRun?: boolean; message?: string } = {},
  home = oocoderHome(),
): Promise<{ commentUrl: string | null; body: string; dryRun: boolean }> {
  const policy = loadPolicy(home);
  const allowlist = loadAllowlist(home);
  const issue = await fetchIssue(issueUrl);

  if (!options.dryRun && !isAllowed(issue.owner, issue.repo, allowlist)) {
    throw new Error(
      `${issue.owner}/${issue.repo} is not on the allowlist. Edit ~/.oocoder/allowlist.yaml or configs/allowlist.yaml`,
    );
  }

  if (!options.dryRun) {
    assertRateLimit("comments", policy.rateLimits.maxCommentsPerDay, home);
  }

  const body =
    options.message?.trim() ||
    [
      `Thanks for opening this issue. I'm looking into **${issue.title}** and will propose a fix if it fits the project's contribution guidelines.`,
      "",
      policy.disclosure.trim(),
    ].join("\n");

  if (options.dryRun) {
    return { commentUrl: null, body, dryRun: true };
  }

  const commentUrl = await postIssueComment(
    issue.owner,
    issue.repo,
    issue.number,
    body,
  );
  bumpRate("comments", home);
  updateCandidate(`${issue.owner}/${issue.repo}#${issue.number}`, {
    status: "discussed",
    notes: "Posted discussion comment",
  }, home);
  logEvent("discuss", commentUrl, { issue: issue.url }, home);

  return { commentUrl, body, dryRun: false };
}
