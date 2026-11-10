export interface Diagnostic {
  kind: "compile" | "test" | "lint" | "raw";
  file: string | null;
  line: number | null;
  message: string;
  source: "annotation" | "job-log" | "status";
}

const MAX_MSG = 500;

function clip(text: string): string {
  const trimmed = text.replace(/\s+/g, " ").trim();
  return trimmed.length > MAX_MSG ? trimmed.slice(0, MAX_MSG) : trimmed;
}

function pushUnique(
  out: Diagnostic[],
  item: Diagnostic,
  seen: Set<string>,
): void {
  const key = `${item.file ?? ""}:${item.line ?? ""}:${item.message}`;
  if (seen.has(key)) return;
  seen.add(key);
  out.push(item);
}

/**
 * Parse CI log text into diagnostics. Ecosystem patterns first, then generic
 * file:line. Unparsed text stays available to the model as a raw tail.
 */
export function parseErrors(
  logText: string,
  source: Diagnostic["source"] = "job-log",
): Diagnostic[] {
  const lines = logText.split(/\r?\n/);
  const out: Diagnostic[] = [];
  const seen = new Set<string>();
  const consumed = new Set<number>();

  const rules: Array<{
    re: RegExp;
    kind: Diagnostic["kind"];
    map: (m: RegExpExecArray) => Omit<Diagnostic, "source">;
  }> = [
    {
      re: /^FAILED\s+(\S+\.py)::(\S+)/,
      kind: "test",
      map: (m) => ({
        kind: "test",
        file: m[1] ?? null,
        line: null,
        message: clip(m[0]),
      }),
    },
    {
      re: /^E\s{3}(.+)$/,
      kind: "test",
      map: (m) => ({
        kind: "test",
        file: null,
        line: null,
        message: clip(m[1] ?? m[0]),
      }),
    },
    {
      re: /^(.+\.py):(\d+):\s+error:\s+(.+)$/,
      kind: "compile",
      map: (m) => ({
        kind: "compile",
        file: m[1] ?? null,
        line: Number(m[2]),
        message: clip(m[3] ?? ""),
      }),
    },
    {
      re: /^(.+\.py):(\d+):\d+:\s+[A-Z]\d+\s+(.+)$/,
      kind: "lint",
      map: (m) => ({
        kind: "lint",
        file: m[1] ?? null,
        line: Number(m[2]),
        message: clip(m[3] ?? ""),
      }),
    },
    {
      re: /^(.+\.(?:ts|tsx|js|jsx|mjs|cjs))\((\d+),\d+\):\s+error\s+TS\d+:\s+(.+)$/,
      kind: "compile",
      map: (m) => ({
        kind: "compile",
        file: m[1] ?? null,
        line: Number(m[2]),
        message: clip(m[3] ?? ""),
      }),
    },
    {
      re: /^(.+\.(?:js|jsx|ts|tsx)):\s+line\s+(\d+),\s+col\s+\d+,\s+Error\s+-\s+(.+)$/,
      kind: "lint",
      map: (m) => ({
        kind: "lint",
        file: m[1] ?? null,
        line: Number(m[2]),
        message: clip(m[3] ?? ""),
      }),
    },
    {
      re: /^●\s+(.+)$/,
      kind: "test",
      map: (m) => ({
        kind: "test",
        file: null,
        line: null,
        message: clip(m[1] ?? m[0]),
      }),
    },
    {
      re: /^Expected\s+.+$/,
      kind: "test",
      map: (m) => ({
        kind: "test",
        file: null,
        line: null,
        message: clip(m[0]),
      }),
    },
    {
      re: /^(\.\/.+\.go):(\d+):\d+:\s+(.+)$/,
      kind: "compile",
      map: (m) => ({
        kind: "compile",
        file: m[1] ?? null,
        line: Number(m[2]),
        message: clip(m[3] ?? ""),
      }),
    },
    {
      re: /^---\s+FAIL:\s+(\S+)/,
      kind: "test",
      map: (m) => ({
        kind: "test",
        file: null,
        line: null,
        message: clip(m[0]),
      }),
    },
    {
      re: /^error\[E\d+\]:.+$/,
      kind: "compile",
      map: (m) => ({
        kind: "compile",
        file: null,
        line: null,
        message: clip(m[0]),
      }),
    },
    {
      re: /^\s+-->\s+(.+\.rs):(\d+):\d+/,
      kind: "compile",
      map: (m) => ({
        kind: "compile",
        file: m[1] ?? null,
        line: Number(m[2]),
        message: "rustc error",
      }),
    },
    {
      re: /^(.+\.(?:c|cc|cpp|h|hpp)):(\d+):\d+:\s+error:\s+(.+)$/,
      kind: "compile",
      map: (m) => ({
        kind: "compile",
        file: m[1] ?? null,
        line: Number(m[2]),
        message: clip(m[3] ?? ""),
      }),
    },
  ];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    for (const rule of rules) {
      const match = rule.re.exec(line);
      if (!match) continue;
      const mapped = rule.map(match);
      pushUnique(out, { ...mapped, source }, seen);
      consumed.add(i);
      break;
    }
  }

  const generic = /^(.+\.[A-Za-z0-9]+):(\d+):\s*(.+)$/;
  for (let i = 0; i < lines.length; i++) {
    if (consumed.has(i)) continue;
    const line = lines[i] ?? "";
    const match = generic.exec(line);
    if (!match) continue;
    pushUnique(
      out,
      {
        kind: "compile",
        file: match[1] ?? null,
        line: Number(match[2]),
        message: clip(match[3] ?? ""),
        source,
      },
      seen,
    );
  }

  if (out.length === 0 && logText.trim()) {
    const tail = lines.slice(-100).join("\n");
    out.push({
      kind: "raw",
      file: null,
      line: null,
      message: clip(tail),
      source,
    });
  }

  return out.slice(0, 25);
}

export function dedupeDiagnostics(items: Diagnostic[]): Diagnostic[] {
  const seen = new Set<string>();
  const out: Diagnostic[] = [];
  for (const item of items) {
    pushUnique(out, item, seen);
  }
  return out.slice(0, 25);
}
