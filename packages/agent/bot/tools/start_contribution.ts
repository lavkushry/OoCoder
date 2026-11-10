import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runOocoder } from "../lib/runOocoder.js";

export default defineTool({
  description:
    "Implement a fix for a GitHub issue via OoCoder (Cursor SDK local by default), run quality gates, and open a PR. Always prefer dryRun first in uncertain cases. Never call this for repos outside the allowlist.",
  effect: "write",
  inputSchema: z.object({
    issue: z.string().describe("Issue URL or owner/repo#number"),
    dryRun: z.boolean().optional(),
    skipPr: z.boolean().optional(),
    skipDiscuss: z.boolean().optional(),
    runtime: z.enum(["local", "cloud"]).optional(),
  }),
  async execute({ issue, dryRun, skipPr, skipDiscuss, runtime }, ctx) {
    if (ctx.session.purpose === "eval") {
      return {
        started: false,
        reason: "eval",
        issue,
        note: "Eval mode skips side effects; quality gate still required before ship in production.",
      };
    }
    const args = ["work", issue];
    if (dryRun) args.push("--dry-run");
    if (skipPr) args.push("--skip-pr");
    if (skipDiscuss) args.push("--skip-discuss");
    if (runtime) args.push("--runtime", runtime);
    const result = await runOocoder(args, { timeoutMs: 60 * 60 * 1000 });
    return {
      ok: result.code === 0,
      output: (result.stdout || result.stderr).trim(),
    };
  },
});
