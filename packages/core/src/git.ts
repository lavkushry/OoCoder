import { existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { runOk, run } from "./shell.js";
import { paths, oocoderHome } from "./paths.js";
import { slugify } from "./github.js";

export async function ensureMirrorClone(options: {
  cloneUrl: string;
  owner: string;
  repo: string;
  upstream: string;
  home?: string;
}): Promise<string> {
  const home = options.home ?? oocoderHome();
  const mirrorDir = join(paths(home).work, "mirrors", options.owner, options.repo);
  mkdirSync(join(mirrorDir, ".."), { recursive: true });

  if (!existsSync(join(mirrorDir, ".git"))) {
    mkdirSync(mirrorDir, { recursive: true });
    await runOk("git", ["clone", options.cloneUrl, mirrorDir]);
  } else {
    await runOk("git", ["fetch", "--all", "--prune"], { cwd: mirrorDir });
  }

  // Ensure upstream remote for forks.
  const remotes = await runOk("git", ["remote"], { cwd: mirrorDir });
  if (!remotes.split("\n").includes("upstream") && options.upstream !== `${options.owner}/${options.repo}`) {
    await runOk(
      "git",
      ["remote", "add", "upstream", `https://github.com/${options.upstream}.git`],
      { cwd: mirrorDir },
    );
  } else if (remotes.split("\n").includes("upstream")) {
    await run("git", ["fetch", "upstream", "--prune"], { cwd: mirrorDir });
  }

  return mirrorDir;
}

export async function createWorktree(options: {
  mirrorDir: string;
  owner: string;
  repo: string;
  issueNumber: number;
  title: string;
  baseBranch: string;
  home?: string;
}): Promise<{ worktreePath: string; branch: string }> {
  const home = options.home ?? oocoderHome();
  const branch = `oocoder/issue-${options.issueNumber}-${slugify(options.title)}`;
  const worktreePath = join(
    paths(home).work,
    "trees",
    options.owner,
    options.repo,
    `issue-${options.issueNumber}`,
  );

  if (existsSync(worktreePath)) {
    rmSync(worktreePath, { recursive: true, force: true });
    await run("git", ["worktree", "prune"], { cwd: options.mirrorDir });
  }
  mkdirSync(join(worktreePath, ".."), { recursive: true });

  // Prefer upstream/base when available.
  const remotes = await runOk("git", ["remote"], { cwd: options.mirrorDir });
  const hasUpstream = remotes.split("\n").includes("upstream");
  const startPoint = hasUpstream
    ? `upstream/${options.baseBranch}`
    : `origin/${options.baseBranch}`;

  await runOk("git", ["fetch", hasUpstream ? "upstream" : "origin", options.baseBranch], {
    cwd: options.mirrorDir,
  });

  // Delete local branch if it already exists.
  await run("git", ["branch", "-D", branch], { cwd: options.mirrorDir });

  await runOk(
    "git",
    ["worktree", "add", "-b", branch, worktreePath, startPoint],
    { cwd: options.mirrorDir },
  );

  return { worktreePath, branch };
}

export async function commitAll(
  cwd: string,
  message: string,
): Promise<boolean> {
  await runOk("git", ["add", "-A"], { cwd });
  const status = await runOk("git", ["status", "--porcelain"], { cwd });
  if (!status) {
    return false;
  }
  await runOk("git", ["commit", "-m", message], {
    cwd,
    env: {
      ...process.env,
      GIT_AUTHOR_NAME: process.env.GIT_AUTHOR_NAME ?? "OoCoder",
      GIT_AUTHOR_EMAIL: process.env.GIT_AUTHOR_EMAIL ?? "oocoder@users.noreply.github.com",
      GIT_COMMITTER_NAME: process.env.GIT_COMMITTER_NAME ?? "OoCoder",
      GIT_COMMITTER_EMAIL:
        process.env.GIT_COMMITTER_EMAIL ?? "oocoder@users.noreply.github.com",
    },
  });
  return true;
}

export function pushArgs(branch: string, remote = "origin"): string[] {
  const protectedBranches = new Set(["main", "master", "develop"]);
  if (protectedBranches.has(branch)) {
    throw new Error(`Refusing to push protected branch: ${branch}`);
  }
  if (branch.startsWith("+") || branch.includes("--force")) {
    throw new Error(`Refusing force-style ref: ${branch}`);
  }
  return ["push", "-u", remote, branch];
}

export async function pushBranch(
  cwd: string,
  branch: string,
  remote = "origin",
): Promise<void> {
  const args = pushArgs(branch, remote);
  if (args.includes("--force") || args.includes("--force-with-lease")) {
    throw new Error("Refusing force-push");
  }
  await runOk("git", args, { cwd });
}

export async function diffStats(cwd: string, baseRef: string): Promise<{
  filesChanged: number;
  linesChanged: number;
}> {
  const nameOnly = await runOk("git", ["diff", "--name-only", baseRef], { cwd });
  const filesChanged = nameOnly
    ? nameOnly.split("\n").filter((l) => l.trim().length > 0).length
    : 0;

  const numstat = await runOk("git", ["diff", "--numstat", baseRef], { cwd });
  let linesChanged = 0;
  for (const line of numstat.split("\n")) {
    if (!line.trim()) continue;
    const [added, removed] = line.split("\t");
    const a = added === "-" ? 0 : Number(added);
    const r = removed === "-" ? 0 : Number(removed);
    if (!Number.isNaN(a)) linesChanged += a;
    if (!Number.isNaN(r)) linesChanged += r;
  }

  return { filesChanged, linesChanged };
}

export async function currentHead(cwd: string): Promise<string> {
  return runOk("git", ["rev-parse", "HEAD"], { cwd });
}
