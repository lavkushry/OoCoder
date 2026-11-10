import {
  defaultGitHubAuth,
  githubChannel,
} from "@grok/agent-sdk/channels/github";

/**
 * Configure watched repos via OOCODER_GITHUB_REPOS=owner/repo,owner/repo
 * (comma-separated). Connect GitHub in Cursor Dashboard, then agent-sdk login.
 */
function watchedRepos(): string[] {
  const raw = process.env.OOCODER_GITHUB_REPOS ?? "";
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

const repos = watchedRepos();

export default githubChannel({
  botName: "oocoder",
  cursorAccount: {
    // Empty list means "configure OOCODER_GITHUB_REPOS before live delivery".
    repos: repos.length > 0 ? repos : ["octocat/Hello-World"],
  },
  onComment: (ctx, comment) => {
    const body = comment.body ?? "";
    const mentions =
      body.includes("@oocoder") || body.toLowerCase().includes("oocoder");
    if (!mentions) {
      return null;
    }
    return {
      auth: defaultGitHubAuth(ctx),
      title: `OoCoder review follow-up ${ctx.repository.fullName}`,
      context: [
        "A human left feedback on an OoCoder-related PR or issue.",
        "Call inspect_pr if this is a pull request, then reply_to_review.",
        "If code changes are needed, call start_contribution with the linked issue when available, or explain the next step.",
        "Always keep diffs small and disclose that OoCoder is an AI agent.",
      ],
    };
  },
  onCheckSuite: (ctx, suite) => {
    if (suite.conclusion !== "failure") {
      return null;
    }
    return {
      auth: defaultGitHubAuth(ctx),
      title: `OoCoder CI failure ${ctx.repository.fullName}`,
      context: [
        "CI failed on a watched repository.",
        "If the head branch starts with oocoder/, call start_contribution only when a linked issue exists; otherwise tell the operator to run `oocoder fixci` for a same-branch repair.",
        "Do not force-push or touch protected branches.",
      ],
    };
  },
});
