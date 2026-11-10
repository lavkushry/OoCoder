import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runOocoder } from "../lib/runOocoder.js";

export default defineTool({
  description:
    "Run OoCoder CI repair on pull requests this agent opened. Defaults to dry-run. Never force-pushes.",
  effect: "write",
  inputSchema: z.object({
    pr: z.string().optional().describe("Pull URL or owner/repo#number"),
    dryRun: z.boolean().optional(),
  }),
  async execute({ pr, dryRun }, ctx) {
    if (ctx.session.purpose === "eval") {
      return { ok: true, reason: "eval", dryRun: true };
    }
    const args = ["fixci"];
    if (dryRun !== false) args.push("--dry-run");
    if (pr) args.push("--pr", pr);
    const result = await runOocoder(args, { timeoutMs: 10 * 60 * 1000 });
    return {
      ok: result.code === 0,
      output: (result.stdout || result.stderr).trim(),
    };
  },
});