import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, normalize, resolve } from "node:path";

export interface GrokFileEdit {
  path: string;
  content: string;
}

export interface GrokEditPlan {
  summary: string;
  files: GrokFileEdit[];
}

export function grokApiKey(): string | undefined {
  const value = process.env.GROK_API_KEY || process.env.XAI_API_KEY;
  return value && value.trim() ? value.trim() : undefined;
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
  };
  if (!Array.isArray(parsed.files)) {
    throw new Error("Grok edit plan is missing a files array");
  }
  const files: GrokFileEdit[] = [];
  for (const item of parsed.files) {
    if (!item || typeof item !== "object") continue;
    const record = item as { path?: unknown; content?: unknown };
    if (typeof record.path !== "string" || typeof record.content !== "string") continue;
    files.push({ path: record.path, content: record.content });
  }
  if (files.length === 0) {
    throw new Error("Grok edit plan did not include any files");
  }
  return {
    summary: typeof parsed.summary === "string" ? parsed.summary : "Grok applied a patch",
    files,
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

export function applyGrokEdits(cwd: string, plan: GrokEditPlan): string[] {
  const written: string[] = [];
  for (const file of plan.files) {
    const target = safeRepoPath(cwd, file.path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, file.content, "utf8");
    written.push(file.path);
  }
  return written;
}

/** Default coding model on api.x.ai (alias stays current). Override with GROK_MODEL. */
export const DEFAULT_GROK_MODEL = "grok-code-fast-1";

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
        {
          role: "system",
          content:
            "You edit a local git repository. Reply with one JSON object only: {\"summary\": string, \"files\": [{\"path\": \"repo-relative\", \"content\": \"full new file\"}]}. No markdown unless the JSON is inside one json fence. Do not include secrets. Prefer the smallest files that need changes; never rewrite huge unrelated files.",
        },
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
