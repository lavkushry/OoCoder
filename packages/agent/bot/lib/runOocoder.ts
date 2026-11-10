import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

function findRepoRoot(): string {
  let dir = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  for (let i = 0; i < 8; i++) {
    if (existsSync(join(dir, "pnpm-workspace.yaml"))) return dir;
    const parent = resolve(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
}

function cliEntry(): string {
  return join(findRepoRoot(), "packages/cli/dist/bin.js");
}

export async function runOocoder(
  args: string[],
  options: { timeoutMs?: number } = {},
): Promise<{ code: number; stdout: string; stderr: string }> {
  const entry = cliEntry();
  if (!existsSync(entry)) {
    throw new Error(
      `OoCoder CLI not built at ${entry}. Run: pnpm --filter @oocoder/cli build`,
    );
  }

  return new Promise((resolvePromise, reject) => {
    const child = spawn(process.execPath, [entry, ...args], {
      env: { ...process.env },
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    const timer =
      options.timeoutMs !== undefined
        ? setTimeout(() => {
            child.kill("SIGTERM");
            reject(new Error(`oocoder timed out: ${args.join(" ")}`));
          }, options.timeoutMs)
        : undefined;

    child.stdout.on("data", (c: Buffer) => {
      stdout += c.toString();
    });
    child.stderr.on("data", (c: Buffer) => {
      stderr += c.toString();
    });
    child.on("error", (err) => {
      if (timer) clearTimeout(timer);
      reject(err);
    });
    child.on("close", (code) => {
      if (timer) clearTimeout(timer);
      resolvePromise({ code: code ?? 1, stdout, stderr });
    });
  });
}
