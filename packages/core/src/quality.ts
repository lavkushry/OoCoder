import { existsSync } from "node:fs";
import { join } from "node:path";
import { run } from "./shell.js";
import { diffStats } from "./git.js";
import type { PolicyConfig, QualityDiffStats } from "./types.js";

export async function checkDiffQuality(
  cwd: string,
  baseRef: string,
  policy: PolicyConfig,
): Promise<QualityDiffStats> {
  const stats = await diffStats(cwd, baseRef);
  if (stats.filesChanged > policy.quality.maxFilesChanged) {
    return {
      ...stats,
      ok: false,
      reason: `Too many files changed (${stats.filesChanged} > ${policy.quality.maxFilesChanged})`,
    };
  }
  if (stats.linesChanged > policy.quality.maxLinesChanged) {
    return {
      ...stats,
      ok: false,
      reason: `Too many lines changed (${stats.linesChanged} > ${policy.quality.maxLinesChanged})`,
    };
  }
  return { ...stats, ok: true };
}

const VERIFY_CANDIDATES: Array<{ cmd: string; args: string[] }> = [
  { cmd: "pnpm", args: ["test"] },
  { cmd: "npm", args: ["test", "--if-present"] },
  { cmd: "yarn", args: ["test"] },
  { cmd: "pytest", args: ["-q"] },
  { cmd: "go", args: ["test", "./..."] },
  { cmd: "cargo", args: ["test"] },
];

/**
 * Makefile verify strategy.
 * Full `make test` on Redis-like trees cleans binaries and can run for hours.
 * Default to compile-only `make`. Opt in with OOCODER_VERIFY_MAKE_TEST=1.
 */
export function makefileVerifyArgs(
  env: NodeJS.ProcessEnv = process.env,
): { args: string[]; command: string; timeoutMs: number } {
  const wantFullTest = env.OOCODER_VERIFY_MAKE_TEST === "1";
  return {
    args: wantFullTest ? ["test"] : [],
    command: wantFullTest ? "make test" : "make",
    timeoutMs: wantFullTest ? 30 * 60 * 1000 : 15 * 60 * 1000,
  };
}

export async function runBestEffortVerify(cwd: string): Promise<{
  ran: boolean;
  command: string | null;
  ok: boolean;
  output: string;
}> {
  // Prefer project scripts if package.json / Makefile present; never fail hard on missing tests.
  if (existsSync(join(cwd, "package.json"))) {
    for (const [cmd, args] of [
      ["pnpm", ["-s", "test"]],
      ["npm", ["test", "--if-present"]],
    ] as const) {
      const result = await run(cmd, [...args], { cwd, timeoutMs: 10 * 60 * 1000 });
      if (cmd === "npm" || result.code === 0 || result.stderr || result.stdout) {
        return {
          ran: true,
          command: `${cmd} ${args.join(" ")}`,
          ok: result.code === 0,
          output: (result.stdout + "\n" + result.stderr).trim(),
        };
      }
    }
  }

  if (existsSync(join(cwd, "Makefile"))) {
    const { args, command, timeoutMs } = makefileVerifyArgs();
    const result = await run("make", args, { cwd, timeoutMs });
    if (result.code === 0 || result.stdout || result.stderr) {
      return {
        ran: true,
        command,
        ok: result.code === 0,
        output: (result.stdout + "\n" + result.stderr).trim(),
      };
    }
  }

  for (const { cmd, args } of VERIFY_CANDIDATES) {
    if (!existsSync(join(cwd, markerFor(cmd)))) continue;
    const result = await run(cmd, args, { cwd, timeoutMs: 10 * 60 * 1000 });
    return {
      ran: true,
      command: `${cmd} ${args.join(" ")}`,
      ok: result.code === 0,
      output: (result.stdout + "\n" + result.stderr).trim(),
    };
  }

  return { ran: false, command: null, ok: true, output: "No verify command detected" };
}

function markerFor(cmd: string): string {
  switch (cmd) {
    case "pytest":
      return "pyproject.toml";
    case "go":
      return "go.mod";
    case "cargo":
      return "Cargo.toml";
    case "pnpm":
    case "npm":
    case "yarn":
      return "package.json";
    default:
      return "package.json";
  }
}
