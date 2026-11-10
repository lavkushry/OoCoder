# @oocoder/agent

Cursor Agent SDK project for OoCoder. Served prompt: `bot/instructions.md`.

## Loop

```bash
npx agent-sdk validate --dir .
npx agent-sdk info --dir . --json
npx agent-sdk serve --dir . --mode single --dev
npx agent-sdk eval --dir .
npm run check
```

Build the CLI first (`pnpm --filter @oocoder/cli build`) — tools shell out to `packages/cli/dist/bin.js`.

## Layout

- `bot/tools/*` — one tool per file (filename = tool name)
- `bot/channels/github.ts` — review / CI follow-ups (`OOCODER_GITHUB_REPOS`)
- `bot/schedules/hourly-discover.md` — cron discovery (manual under `--dev`)
- `bot/lib/runOocoder.ts` — CLI bridge (no direct `@oocoder/core` import)

## Do not

- Reinvent coding loops — Cursor SDK / CLI owns implementation
- Skip allowlist / disclosure / quality gates
