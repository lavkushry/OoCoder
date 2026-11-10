import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";

export default defineTool({
  description:
    "Inspect the current GitHub pull request for this session (title, body, files, checks). Use before replying to review comments or fixing CI.",
  effect: "read",
  inputSchema: z.object({
    prUrl: z.string().optional().describe("Optional PR URL; defaults to session PR"),
  }),
  async execute({ prUrl }, ctx) {
    const octokit = await ctx.host.github.getOctokit();
    let owner: string | undefined;
    let repo: string | undefined;
    let number: number | undefined;

    if (prUrl) {
      const match = /github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/.exec(prUrl);
      if (!match) throw new Error(`Invalid PR URL: ${prUrl}`);
      owner = match[1];
      repo = match[2];
      number = Number(match[3]);
    } else {
      const key = ctx.session.continuationKey ?? "";
      const match = /([^/:]+)\/([^#:]+)#(\d+)/.exec(key);
      if (!match) {
        return {
          ok: false,
          reason: "No PR in session continuation key; pass prUrl",
        };
      }
      owner = match[1];
      repo = match[2];
      number = Number(match[3]);
    }

    const { data: pr } = await octokit.rest.pulls.get({
      owner: owner!,
      repo: repo!,
      pull_number: number!,
    });
    const { data: files } = await octokit.rest.pulls.listFiles({
      owner: owner!,
      repo: repo!,
      pull_number: number!,
      per_page: 100,
    });

    return {
      ok: true,
      title: pr.title,
      body: pr.body,
      author: pr.user?.login ?? null,
      draft: pr.draft,
      state: pr.state,
      head: pr.head.ref,
      base: pr.base.ref,
      htmlUrl: pr.html_url,
      files: files.map((f) => ({
        filename: f.filename,
        status: f.status,
        additions: f.additions,
        deletions: f.deletions,
      })),
    };
  },
});
