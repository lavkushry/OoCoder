import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runOocoder } from "../lib/runOocoder.js";

export default defineTool({
  description:
    "Post an investigating / clarifying comment on a GitHub issue with OoCoder AI disclosure. Use before or while starting a contribution.",
  effect: "write",
  inputSchema: z.object({
    issue: z
      .string()
      .describe("Issue URL or owner/repo#number"),
    message: z.string().optional(),
    dryRun: z.boolean().optional(),
  }),
  async execute({ issue, message, dryRun }, ctx) {
    if (ctx.session.purpose === "eval") {
      return { posted: false, reason: "eval", issue };
    }
    const args = ["discuss", issue];
    if (dryRun) args.push("--dry-run");
    if (message) args.push("--message", message);
    const result = await runOocoder(args, { timeoutMs: 60_000 });
    return {
      ok: result.code === 0,
      output: (result.stdout || result.stderr).trim(),
    };
  },
});
