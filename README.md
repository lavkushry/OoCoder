# OoCoder

Autonomous open-source contribution agent for Linux (and macOS).

OoCoder **discovers** suitable GitHub issues, **joins discussions** with clear AI disclosure, **implements** fixes with the Cursor SDK on your machine, **opens PRs** via `gh`, and **iterates** on review/CI through a Cursor Agent SDK GitHub bot.

It reuses the proven OSS-agent pipeline (discover → isolate → implement → verify → PR → iterate) instead of inventing a new coding loop.

## Architecture

```
OoCoder/
  configs/                 # allowlist + policy (safety rails)
  packages/core/           # shared pipeline (state, gh/git, Cursor SDK)
  packages/cli/            # `oocoder` — agent on your machine
  packages/agent/          # Agent SDK bot (schedule + GitHub follow-ups)
```

| Trigger | What runs |
| --- | --- |
| `oocoder discover/work` | Local CLI orchestrator + Cursor SDK |
| Agent SDK schedule | Hourly discover → optional auto-contribute |
| GitHub channel | Review comments / CI failures on watched repos |

## Prerequisites

- Node.js **22.13+**
- `git` and GitHub CLI (`gh auth login`)
- Cursor API key: `export CURSOR_API_KEY=...`
- (Agent bot) `cd packages/agent && npx agent-sdk login`
- (Agent bot) GitHub connected in [Cursor Dashboard](https://cursor.com/dashboard)

## Quick start (CLI)

```bash
pnpm install
pnpm --filter @oocoder/core build
pnpm --filter @oocoder/cli build

# Create ~/.oocoder and check auth
pnpm --filter @oocoder/cli exec node ./dist/bin.js init

# Allowlist a repo you may fork/contribute to
# edit ~/.oocoder/allowlist.yaml  →  repos: ["owner/repo"]

# Find candidates
pnpm --filter @oocoder/cli exec node ./dist/bin.js discover --limit 10

# Dry-run a contribution (no Cursor call / no PR)
pnpm --filter @oocoder/cli exec node ./dist/bin.js work \
  https://github.com/owner/repo/issues/123 --dry-run

# Real run on your Linux box (local Cursor agent → quality gates → gh pr)
export CURSOR_API_KEY=...
pnpm --filter @oocoder/cli exec node ./dist/bin.js work \
  https://github.com/owner/repo/issues/123
```

Optional cloud path (Cursor-hosted VM + `autoCreatePR`):

```bash
pnpm --filter @oocoder/cli exec node ./dist/bin.js work <issue-url> --runtime cloud
```

### CLI commands

| Command | Purpose |
| --- | --- |
| `init` | Create `~/.oocoder`, copy configs, auth check |
| `discover` | Search issues by labels / language |
| `discuss` | Post investigating comment + disclosure |
| `work` | Implement + quality gate + PR |
| `status` | Candidates + policy snapshot |
| `daemon` | Discover → work top N allowlisted issues |

## Safety rails (required for full autonomy)

Configured in [`configs/policy.yaml`](configs/policy.yaml) / `~/.oocoder/policy.yaml`:

- **Allowlist only** — [`configs/allowlist.yaml`](configs/allowlist.yaml)
- **AI disclosure** on comments and PR bodies
- **Quality caps** — max files / lines changed
- **Rate limits** — issues / PRs / comments per day
- No force-push; no commits to `main` / `master` / `develop`
- `--dry-run` and `--skip-pr` for safe testing

## Agent SDK bot

```bash
cd packages/agent
npx agent-sdk validate --dir .
npx agent-sdk login
export OOCODER_GITHUB_REPOS=owner/repo   # watched repos for follow-ups
npx agent-sdk serve --dir . --mode single --dev
# Playground: http://127.0.0.1:3000/playground
```

Tools: `discover_issues`, `post_issue_comment`, `start_contribution`, `inspect_pr`, `reply_to_review`.

Schedule `hourly-discover` does **not** auto-fire under `--dev`. Dispatch manually:

```bash
curl -X POST http://127.0.0.1:3000/agent/v1/dev/schedules/hourly-discover
```

Smoke eval (needs credentials for a live model turn):

```bash
cd packages/agent && npx agent-sdk eval --dir .
```

## Portfolio demo script

1. `oocoder init` and add one sandbox / friendly repo to the allowlist  
2. `oocoder discover --limit 5` — show ranked candidates  
3. `oocoder discuss <issue> --dry-run` — show disclosure comment  
4. `oocoder work <issue> --dry-run` — show worktree preparation  
5. `oocoder work <issue>` — open a real PR, paste the URL  
6. (Optional) Agent playground: ask it to discover and dry-run contribute  

## v0.2 — CI repair and stats

`oocoder fixci` looks at pull requests this agent opened. When GitHub Actions (or a legacy commit status) is red on an `oocoder/*` branch in your fork, it downloads diagnostics, asks Cursor for a minimal repair, runs tests, and pushes a normal commit to the **same branch**. It never force-pushes. After two failed attempts it drafts a stand-down comment and stores a lesson.

```bash
pnpm oocoder fixci --dry-run
pnpm oocoder fixci --pr https://github.com/owner/repo/pull/42
pnpm oocoder dashboard --no-refresh --out stats.html
```

Job logs need a GitHub token with **Actions: Read**. Without that scope, repair falls back to check annotations and combined status and logs `ci logs unavailable (token lacks Actions:Read)`.

Policy keys (defaults apply if an older `~/.oocoder/policy.yaml` omits them):

```yaml
ciFix:
  enabled: true
  maxAttemptsPerPr: 2
  maxFixesPerRun: 1
  minMinutesAfterPush: 10
  maxFilesTouched: 3
  maxLogBytes: 60000
telemetry:
  enabled: true
dashboard:
  refreshPrStates: true
  historyDays: 30
```

`oocoder daemon` runs one CI repair pass after discovery. `dashboard` writes a single offline `stats.html` (KPI, timeline, funnel, repos, PR lifecycle, CI fixes, friction, lessons).

## Environment

| Variable | Purpose |
| --- | --- |
| `CURSOR_API_KEY` | Cursor SDK coding runs |
| `OOCODER_HOME` | Override state dir (default `~/.oocoder`) |
| `OOCODER_GITHUB_REPOS` | Comma-separated repos for Agent SDK GitHub channel |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` | Optional commit identity (defaults to OoCoder) |

## License

MIT — see [LICENSE](LICENSE).
