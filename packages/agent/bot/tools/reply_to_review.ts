import { parseGitHubPrContinuationKey } from "@grok/agent-sdk/channels/github";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";

export default defineTool({
  description:
    "Post a reply comment on the session pull request after addressing review feedback. Include OoCoder disclosure when appropriate.",
  effect: "write",
  inputSchema: z.object({
    body: z.string().describe("Markdown comment to post on the PR"),
  }),
  async execute({ body }, ctx) {
    if (ctx.session.purpose === "eval") {
      return { posted: false, reason: "eval" };
    }
    const ref = parseGitHubPrContinuationKey(ctx.session.continuationKey ?? "");
    if (ref === undefined) {
      throw new Error("reply_to_review requires a GitHub pull request session");
    }
    const octokit = await ctx.host.github.getOctokit();
    const { data } = await octokit.rest.issues.createComment({
      owner: ref.owner,
      repo: ref.repo,
      issue_number: ref.number,
      body,
    });
    return {
      posted: true,
      commentUrl: data.html_url,
      pr: `${ref.owner}/${ref.repo}#${ref.number}`,
    };
  },
});
