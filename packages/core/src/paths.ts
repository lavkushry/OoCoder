import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { existsSync } from "node:fs";

/** Resolve project root (repo containing configs/). */
export function findProjectRoot(start = process.cwd()): string {
  let dir = resolve(start);
  for (let i = 0; i < 12; i++) {
    if (existsSync(join(dir, "configs", "allowlist.yaml"))) {
      return dir;
    }
    if (existsSync(join(dir, "pnpm-workspace.yaml"))) {
      return dir;
    }
    const parent = resolve(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return resolve(start);
}

export function oocoderHome(override?: string): string {
  return override ?? process.env.OOCODER_HOME ?? join(homedir(), ".oocoder");
}

export function paths(home = oocoderHome()) {
  return {
    home,
    db: join(home, "oocoder.db"),
    work: join(home, "work"),
    logs: join(home, "logs"),
    allowlist: join(home, "allowlist.yaml"),
    policy: join(home, "policy.yaml"),
  };
}

export function projectConfigPaths(root = findProjectRoot()) {
  return {
    root,
    allowlist: join(root, "configs", "allowlist.yaml"),
    policy: join(root, "configs", "policy.yaml"),
  };
}
