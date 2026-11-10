import { Agent } from "@cursor/sdk";
import { grokApiKey, implementWithGrok } from "./grok.js";
import { noteUsage } from "./telemetry.js";
import type { IssueRef, RuntimeMode } from "./types.js";

function buildPrompt(issue: IssueRef): string {
  return [
    "You are OoCoder, an autonomous open-source contributor.",
    "Implement a minimal, high-quality fix for the following GitHub issue.",
    "",
    "Rules:",
    "- Stay scoped to this issue; do not refactor unrelated code.",
    "- Prefer small diffs; follow existing style and contribution guidelines if present (CONTRIBUTING.md, AGENTS.md).",
    "- Add or update tests when appropriate and cheap.",
    "- Do not commit secrets, force-push, or modify CI credentials.",
    "- When done, leave the working tree with the fix applied (do not open the PR yourself).",
    "",
    `Repository: ${issue.owner}/${issue.repo}`,
    `Issue: #${issue.number} — ${issue.title}`,
    `URL: ${issue.url}`,
    "",
    "Issue body:",
    issue.body || "(empty)",
  ].join("\n");
}

export interface ImplementResult {
  agentId: string;
  status: string;
  resultText: string | null;
  runtime: RuntimeMode;
  prUrl: string | null;
}

export function buildRepairPrompt(input: {
  title: string;
  number: number;
  branch: string;
  sha: string;
  failedChecks: string[];
  diagnostics: string;
}): string {
  return [
    "You are OoCoder repairing CI on an existing pull request.",
    "This is a REPAIR commit: minimal diff, no refactors, no drive-by changes, no new dependencies.",
    "The local test suite MUST pass before you finish.",
    "Do not modify files unrelated to the diagnostics.",
    "Do not force-push. Do not open a new pull request.",
    "",
    `PR #${input.number} "${input.title}" (branch ${input.branch}, head sha ${input.sha})`,
    `Failed checks: ${input.failedChecks.join(", ") || "(unknown)"}`,
    "--- Diagnostics (highest signal first) ---",
    input.diagnostics || "(none parsed)",
    "--- Constraints ---",
    "- Touch at most a few files that the diagnostics name.",
    "- Leave the working tree with the fix applied.",
  ].join("\n");
}

export async function implementIssue(options: {
  issue: IssueRef;
  cwd: string;
  runtime: RuntimeMode;
  model?: string;
  dryRun?: boolean;
  promptOverride?: string;
}): Promise<ImplementResult> {
  if (options.dryRun) {
    return {
      agentId: "dry-run",
      status: "finished",
      resultText: "Dry run — skipped Cursor SDK implementation",
      runtime: options.runtime,
      prUrl: null,
    };
  }

  const apiKey = process.env.CURSOR_API_KEY;
  const hasCursorKey = Boolean(
    apiKey || process.env.CURSOR_API_KEY_FILE || process.env.CURSOR_SERVICE_ACCOUNT_KEY,
  );
  const prompt = options.promptOverride ?? buildPrompt(options.issue);

  if (options.runtime === "local" && !hasCursorKey) {
    if (!grokApiKey()) {
      throw new Error(
        "Set CURSOR_API_KEY for the Cursor SDK, or GROK_API_KEY for the Grok coding path. Or use --dry-run.",
      );
    }
    const grokModel =
      options.model && !options.model.startsWith("composer") ? options.model : undefined;
    const grok = await implementWithGrok({
      cwd: options.cwd,
      prompt,
      ...(grokModel ? { model: grokModel } : {}),
    });
    noteUsage(grok.promptTokens, grok.completionTokens);
    return {
      agentId: "grok",
      status: "finished",
      resultText: `${grok.summary}\nFiles: ${grok.files.join(", ")}`,
      runtime: "local",
      prUrl: null,
    };
  }

  const model = { id: options.model ?? "composer-2.5" };

  if (options.runtime === "cloud") {
    const agent = await Agent.create({
      ...(apiKey ? { apiKey } : {}),
      model,
      cloud: {
        repos: [
          {
            url: `https://github.com/${options.issue.owner}/${options.issue.repo}`,
          },
        ],
        autoCreatePR: true,
        skipReviewerRequest: true,
      },
    });

    const runHandle = await agent.send(prompt);
    const result = await runHandle.wait();
    const prUrl =
      typeof (result as { prUrl?: string }).prUrl === "string"
        ? (result as { prUrl?: string }).prUrl!
        : null;

    return {
      agentId: agent.agentId,
      status: result.status,
      resultText: result.result ?? null,
      runtime: "cloud",
      prUrl,
    };
  }

  await using agent = await Agent.create({
    ...(apiKey ? { apiKey } : {}),
    model,
    local: {
      cwd: options.cwd,
      settingSources: ["project"],
    },
  });

  const runHandle = await agent.send(prompt);
  const result = await runHandle.wait();

  return {
    agentId: agent.agentId,
    status: result.status,
    resultText: result.result ?? null,
    runtime: "local",
    prUrl: null,
  };
}
