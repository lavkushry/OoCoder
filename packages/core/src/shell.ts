import { spawn } from "node:child_process";

export interface RunResult {
  code: number;
  stdout: string;
  stderr: string;
}

export interface RunOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  input?: string;
  timeoutMs?: number;
}

export function run(
  command: string,
  args: string[],
  options: RunOptions = {},
): Promise<RunResult> {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: { ...process.env, ...options.env },
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let settled = false;

    const timer =
      options.timeoutMs !== undefined
        ? setTimeout(() => {
            child.kill("SIGTERM");
            if (!settled) {
              settled = true;
              reject(new Error(`Command timed out: ${command} ${args.join(" ")}`));
            }
          }, options.timeoutMs)
        : undefined;

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    if (options.input !== undefined) {
      child.stdin.write(options.input);
      child.stdin.end();
    } else {
      child.stdin.end();
    }

    child.on("error", (err) => {
      if (timer) clearTimeout(timer);
      if (!settled) {
        settled = true;
        reject(err);
      }
    });

    child.on("close", (code) => {
      if (timer) clearTimeout(timer);
      if (!settled) {
        settled = true;
        resolvePromise({ code: code ?? 1, stdout, stderr });
      }
    });
  });
}

export async function runOk(
  command: string,
  args: string[],
  options: RunOptions = {},
): Promise<string> {
  const result = await run(command, args, options);
  if (result.code !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} failed (${result.code}): ${result.stderr || result.stdout}`,
    );
  }
  return result.stdout.trim();
}

export async function which(binary: string): Promise<boolean> {
  const result = await run("which", [binary]);
  return result.code === 0;
}
