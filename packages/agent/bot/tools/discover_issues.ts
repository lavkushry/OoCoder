import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runOocoder } from "../lib/runOocoder.js";

export default defineTool({
  description:
    "Search GitHub for open OSS contribution candidates (good first issue / help wanted) and persist them. Respects the OoCoder allowlist when configured.",
  effect: "read",
  inputSchema: z.object({
    limit: z.number().int().min(1).max(30).optional(),
    language: z.string().optional(),
    label: z.array(z.string()).optional(),
  }),
  async execute({ limit, language, label }) {
    const args = ["discover", "--limit", String(limit ?? 10)];
    if (language) args.push("--lang", language);
    if (label?.length) {
      args.push("--label", ...label);
    }
    const result = await runOocoder(args, { timeoutMs: 120_000 });
    return {
      ok: result.code === 0,
      output: (result.stdout || result.stderr).trim(),
    };
  },
});
