import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, normalize, resolve } from "node:path";

/** Full-file write (new files or tiny files only). */
export interface GrokFileEdit {
  path: string;
  content: string;
}

/** Surgical in-place replacement. Prefer this for existing sources. */
export interface GrokSearchReplaceEdit {
  path: string;
  old: string;
  new: string;
}

export interface GrokEditPlan {
  summary: string;
  files: GrokFileEdit[];
  edits: GrokSearchReplaceEdit[];
}

/**
 * Resolve an xAI API key.
 * Prefer `XAI_API_KEY`. If both are set, prefer a value that looks like an xAI
 * Console key (`xai-…`) so a stale `GROK_API_KEY` chat token cannot shadow it.
 */
export function grokApiKey(): string | undefined {
  const grok = process.env.GROK_API_KEY?.trim();
  const xai = process.env.XAI_API_KEY?.trim();
  const candidates = [xai, grok].filter((v): v is string => Boolean(v));
  if (candidates.length === 0) return undefined;
  const preferred = candidates.find((v) => v.startsWith("xai-"));
  return preferred ?? candidates[0];
}

export function parseGrokEdits(text: string): GrokEditPlan {
  const fenced = /```(?:json)?\s*([\s\S]*?)```/.exec(text);
  const raw = (fenced?.[1] ?? text).trim();
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start < 0 || end <= start) {
    throw new Error("Grok response did not contain a JSON edit plan");
  }
  const parsed = JSON.parse(raw.slice(start, end + 1)) as {
    summary?: unknown;
    files?: unknown;
    edits?: unknown;
  };

  const files: GrokFileEdit[] = [];
  if (Array.isArray(parsed.files)) {
    for (const item of parsed.files) {
      if (!item || typeof item !== "object") continue;
      const record = item as { path?: unknown; content?: unknown };
      if (typeof record.path !== "string" || typeof record.content !== "string") continue;
      files.push({ path: record.path, content: record.content });
    }
  }

  const edits: GrokSearchReplaceEdit[] = [];
  if (Array.isArray(parsed.edits)) {
    for (const item of parsed.edits) {
      if (!item || typeof item !== "object") continue;
      const record = item as { path?: unknown; old?: unknown; new?: unknown };
      if (
        typeof record.path !== "string" ||
        typeof record.old !== "string" ||
        typeof record.new !== "string"
      ) {
        continue;
      }
      edits.push({ path: record.path, old: record.old, new: record.new });
    }
  }

  if (files.length === 0 && edits.length === 0) {
    throw new Error("Grok edit plan did not include any files or edits");
  }
  return {
    summary: typeof parsed.summary === "string" ? parsed.summary : "Grok applied a patch",
    files,
    edits,
  };
}

export function safeRepoPath(cwd: string, relativePath: string): string {
  const trimmed = relativePath.trim().replaceAll("\\", "/");
  if (!trimmed || trimmed.startsWith("/") || trimmed.includes("\0")) {
    throw new Error(`Refusing unsafe path: ${relativePath}`);
  }
  const root = resolve(cwd);
  const target = resolve(root, normalize(trimmed));
  if (target !== root && !target.startsWith(`${root}/`)) {
    throw new Error(`Refusing path outside the repo: ${relativePath}`);
  }
  return target;
}

/** Reject full-file rewrites that gut an existing large source file. */
export function assertSafeFullFileWrite(
  existing: string | null,
  next: string,
  relativePath: string,
): void {
  if (existing === null) return;
  const oldLines = existing.split("\n").length;
  const newLines = next.split("\n").length;
  if (oldLines < 200) return;
  if (newLines >= Math.floor(oldLines * 0.5)) return;
  throw new Error(
    `Refusing destructive rewrite of ${relativePath}: ${oldLines} → ${newLines} lines (use edits[].old/new instead of files[].content)`,
  );
}

export function applyGrokEdits(cwd: string, plan: GrokEditPlan): string[] {
  const touched = new Set<string>();

  for (const edit of plan.edits) {
    const target = safeRepoPath(cwd, edit.path);
    if (!existsSync(target)) {
      throw new Error(`Edit target does not exist: ${edit.path}`);
    }
    const current = readFileSync(target, "utf8");
    if (!current.includes(edit.old)) {
      throw new Error(`Edit old_string not found in ${edit.path}`);
    }
    if (edit.old === edit.new) continue;
    const occurrences = current.split(edit.old).length - 1;
    if (occurrences !== 1) {
      throw new Error(
        `Edit old_string in ${edit.path} matched ${occurrences} times; need exactly 1`,
      );
    }
    writeFileSync(target, current.replace(edit.old, edit.new), "utf8");
    touched.add(edit.path);
  }

  for (const file of plan.files) {
    const target = safeRepoPath(cwd, file.path);
    const existing = existsSync(target) ? readFileSync(target, "utf8") : null;
    assertSafeFullFileWrite(existing, file.content, file.path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, file.content, "utf8");
    touched.add(file.path);
  }

  return [...touched];
}

/** Default coding model on api.x.ai (alias stays current). Override with GROK_MODEL. */
export const DEFAULT_GROK_MODEL = "grok-code-fast-1";

export const GROK_SYSTEM_PROMPT = [
  "You edit a local git repository with the smallest possible diff.",
  "Reply with one JSON object only:",
  '{"summary": string, "edits": [{"path": "repo-relative", "old": "exact existing snippet", "new": "replacement"}], "files": [{"path": "repo-relative", "content": "full new file"}]}',
  "Rules:",
  "- Prefer edits[] search/replace for any existing file. old must match the file exactly once.",
  "- Use files[] only for brand-new files, or for tiny files under ~200 lines.",
  "- Never rewrite a large existing source file via files[].content.",
  "- No markdown unless the JSON is inside one json fence. Do not include secrets.",
].join(" ");

export function formatGrokHttpError(status: number, body: unknown): string {
  const hint =
    status === 400 || status === 401
      ? " Use an xAI Console key (usually starts with xai-), not a Cursor/Grok chat token. Set XAI_API_KEY or GROK_API_KEY."
      : "";
  if (body && typeof body === "object") {
    const err = (body as { error?: unknown }).error;
    if (typeof err === "string" && err.trim()) {
      return `Grok request failed (${status}): ${err}.${hint}`;
    }
    if (err && typeof err === "object") {
      const message = (err as { message?: unknown }).message;
      if (typeof message === "string" && message.trim()) {
        return `Grok request failed (${status}): ${message}.${hint}`;
      }
    }
    try {
      return `Grok request failed (${status}): ${JSON.stringify(body).slice(0, 500)}.${hint}`;
    } catch {
      /* fall through */
    }
  }
  return `Grok request failed (${status}): unknown error.${hint}`;
}

export async function grokChat(options: {
  apiKey: string;
  model: string;
  prompt: string;
}): Promise<{ text: string; promptTokens: number; completionTokens: number }> {
  const response = await fetch("https://api.x.ai/v1/chat/completions", {
    method: "POST",
    headers: {
      authorization: `Bearer ${options.apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: options.model,
      temperature: 0,
      messages: [
        { role: "system", content: GROK_SYSTEM_PROMPT },
        { role: "user", content: options.prompt },
      ],
    }),
  });
  const body = (await response.json()) as {
    error?: { message?: string } | string;
    choices?: Array<{ message?: { content?: string } }>;
    usage?: { prompt_tokens?: number; completion_tokens?: number };
  };
  if (!response.ok) {
    throw new Error(formatGrokHttpError(response.status, body));
  }
  const text = body.choices?.[0]?.message?.content ?? "";
  if (!text.trim()) {
    throw new Error("Grok returned an empty response");
  }
  return {
    text,
    promptTokens: body.usage?.prompt_tokens ?? 0,
    completionTokens: body.usage?.completion_tokens ?? 0,
  };
}

export async function implementWithGrok(options: {
  cwd: string;
  prompt: string;
  model?: string;
}): Promise<{ summary: string; files: string[]; promptTokens: number; completionTokens: number }> {
  const apiKey = grokApiKey();
  if (!apiKey) {
    throw new Error("GROK_API_KEY or XAI_API_KEY is not set");
  }
  const chat = await grokChat({
    apiKey,
    model: options.model ?? process.env.GROK_MODEL ?? DEFAULT_GROK_MODEL,
    prompt: options.prompt,
  });
  const plan = parseGrokEdits(chat.text);
  const files = applyGrokEdits(options.cwd, plan);
  return {
    summary: plan.summary,
    files,
    promptTokens: chat.promptTokens,
    completionTokens: chat.completionTokens,
  };
}
