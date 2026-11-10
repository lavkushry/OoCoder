import { Agent } from "@cursor/sdk";
import { hasUsableCursorAuth } from "./github.js";
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
    "- For existing large C/source files, return JSON edits[] with exact old/new snippets only — never files[].content rewrites.",
    "- Never invent CLI flags or settings. Verify flags against --help, source, or existing docs before documenting them.",
    "- For duckdb/duckdb-web: use Chicago-style headline capitalization for headings and PR titles (see CONTRIBUTING.md). Never claim storage versions absent from the storage version table. Only document SQL that runs on current DuckDB (e.g. CASE comma-WHEN equality operands; skip comparison-operator WHEN forms that do not parse). Document fsync_mode (STANDARD/NONE/FULL), not the removed enable_fsync boolean.",
    "- For Redis command JSON changes under src/commands/: regenerate src/commands.def with utils/generate-command-code.py.",
    "- For Redis key-memory-histograms / updateSlotAllocSize bugs: do not wrap setTypeIsMemberLazy (or similar lazy helpers) with a second updateSlotAllocSize; do not wrap setTypeExpireIfNeeded/setTypeExpire either (expire already tracks and may free the object); pass setDeleted when lazy expiry can delete the key; early-return readers that set *_NO_UPDATE_ALLOCSIZES must still refresh allocsizes after a rehash; when updating once per argument, skip duplicate key objects; list range helpers must take the real key (not argv[1]) and mutating callers must re-baseline alloc size after the range reply before listElementsRemoved; hashtable encoding tests must lower hash-max-listpack-entries (default 512).",
    "- For Meltano docs: loader namespace is usually destination dialect (not tap_*); Singer PKs use table-key-properties; --full-refresh still respects start_date; env_specific is a store preference not a guarantee.",
    "- For apache/iceberg docs: avoid consecutive blank lines (markdownlint MD012 / Docs Build CI). PR titles must match Module: Description (e.g. Docs: Clarify …); lowercase docs: fails check-pr-title.",
    "- Run or mentally check every SQL/code sample you add; drop examples that do not execute on current main.",
    "- Prefer enable/disable settings over fictional --only-style selectors when isolating services.",
    "- For trinodb/trino Sphinx docs: avoid non-consecutive heading jumps (H2→H4 fails myst.header under sphinx -W); use ### under ##. Commit titles are Capitalized imperative without a docs: prefix.",
    "- No OoCoder, Cursor, or 'Made with Cursor' branding in commits, PR titles, or PR bodies.",
    "- For apache/airflow: PR titles must be plain prose (no Conventional Commits prefixes like 'docs:' / 'fix:').",
    "- For trinodb/trino: commit titles Capitalized imperative ≤60 chars (no 'docs:' prefix); put 'Fixes #N' on a markdown list item in the PR body; prefer .github/DEVELOPMENT.md over duplicating contributor notes in Sphinx develop.md. CLA is email-based (PDF to cla@trino.io via github.com/trinodb/cla), not cla-assistant.io.",
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

  const apiKey = process.env.CURSOR_API_KEY?.trim();
  const hasCursorKey = hasUsableCursorAuth();
  const prompt = options.promptOverride ?? buildPrompt(options.issue);

  const runGrok = async (): Promise<ImplementResult> => {
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
  };

  // Local + no usable Cursor key → Grok (also covers CURSOR_API_KEY='…' placeholders).
  if (options.runtime === "local" && !hasCursorKey) {
    return runGrok();
  }

  const model = { id: options.model ?? "composer-2.5" };
  const cursorApiKeyArg =
    apiKey && hasCursorKey ? { apiKey } : ({} as { apiKey?: string });

  try {
    if (options.runtime === "cloud") {
      const agent = await Agent.create({
        ...cursorApiKeyArg,
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
      ...cursorApiKeyArg,
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
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (options.runtime === "local" && grokApiKey() && /network request failed/i.test(msg)) {
      return runGrok();
    }
    throw new Error(
      `Cursor SDK failed (${msg}). ${
        grokApiKey()
          ? "Unset a placeholder CURSOR_API_KEY or fix network, or rely on GROK_API_KEY fallback."
          : "Set a real CURSOR_API_KEY, or unset it and use GROK_API_KEY."
      }`,
    );
  }
}
