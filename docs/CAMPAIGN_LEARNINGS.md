# DE campaign learnings (encode into prompts / process)

Captured from maintainer review on the first 50+ PRs. Keep `packages/core/src/implement.ts` in sync.

**New learning?** Append first to the dated [rule log](CAMPAIGN_RULE_LOG.md) (`oocoder campaign learn …`), then roll the durable rule into the topic section below.

## Cross-cutting

- No OoCoder / Cursor / "Made with Cursor" branding in commits, titles, or PR bodies. Scrub with `oocoder campaign scrub`.
- Never invent CLI flags or settings. Prefer enable/disable settings over fictional `--only` selectors.
- Run or mentally check every SQL/code sample; drop examples that do not execute on current main.
- Prefer small, single-concern docs PRs over drive-by rewrites.

## apache/airflow

- PR titles must be plain prose. Conventional Commits prefixes (`docs:`, `fix:`, …) fail static checks.

## apache/iceberg

- Docs Build CI runs markdownlint; consecutive blank lines (`MD012`) fail the build — collapse blanks before push.
- PR titles must match `Module: Description` (e.g. `Docs: Clarify …`); lowercase `docs:` fails `check-pr-title`.

## trinodb/trino

- Commit titles: Capitalized imperative, ≤60 chars, no trailing period, no lowercase start (`docs:` fails).
- Issue-closing refs (`Fixes #123`) must sit on a markdown list item (`- Fixes #123`).
- CLA must be on file for CI (`cla-signed`). Trino does **not** use cla-assistant.io; download the [Individual CLA PDF](https://github.com/trinodb/cla/raw/master/Trino%20Foundation%20Individual%20CLA.pdf), sign it, and email to `cla@trino.io` (processing may take a few days). Until `trinodb/cla` contributors lists your GitHub login, every Trino PR stays red even if approved — emailing is not instant.
- Prefer putting contributor tooling notes in `.github/DEVELOPMENT.md` rather than duplicating them in Sphinx `develop.md`.
- Sphinx builds use `-W`; MyST rejects non-consecutive heading jumps (`H2` → `H4`). Use `###` under an `##` section.

## airbytehq/airbyte

- CLA assistant must be signed (`cla-assistant` bot) or docs PRs stay blocked.

## duckdb / duckdb-web

- Chicago-style headline capitalization for headings and PR titles (`CONTRIBUTING.md`).
- Do not claim storage versions that are not in the storage version table.
- Capturing-group examples must show string types (`['33', '14']`, not `[33, 14]`).
- Apply maintainer suggestion commits promptly; reply and re-request review after pushes.
- Only document SQL that runs on current DuckDB (e.g. simple `CASE` comma-WHEN equality operands; comparison-operator WHEN forms may not parse yet).
- `enable_fsync` was renamed to `fsync_mode` (`STANDARD` / `NONE` / `FULL`); document the enum, do not revive the boolean name.

## Prefect

- `prefect server services start` has no `--only`. Isolate services with `PREFECT_API_SERVICES_*_ENABLED`.

## Meltano

- `database_uri` vs `state_backend.uri`: default state is in system DB; external backends still need serialized writers per state ID.
- SQLite → Postgres cutover: stop writers, back up the active SQLite path (respect `MELTANO_SYS_DIR_ROOT`), install driver extras, note that re-adding plugins does not restore state/settings.
- `--full-refresh` still respects `start_date`; custom `_state` files are not rewritten by `meltano state`.
- Loader `namespace` is usually the destination dialect / dbt target, not the extractor `tap_*` family name.
- Singer primary-key metadata is `table-key-properties`, not `key-properties`.
- `env_specific: true` is a store preference, not a guarantee the value stays out of `meltano.yml`.

## Redis

- After editing `src/commands/*.json`, regenerate `src/commands.def` with `utils/generate-command-code.py`.
- Prefer `setTypeIsMemberLazy` / existing lazy helpers for alloc tracking; do not wrap them with a second `updateSlotAllocSize` (double-counts rehash shrinks). Always pass `setDeleted` when lazy expiry can remove the key.
- Do not wrap `setTypeExpireIfNeeded` / `setTypeExpire` in another `updateSlotAllocSize` — expire already refreshes tracking and may free the object under sync deletion (`SRANDMEMBER` count path).
- Early-return readers that set `*_NO_UPDATE_ALLOCSIZES` must still refresh slot allocsizes when a rehash may have finished (e.g. no-op `HGETDEL`, counted `SRANDMEMBER`).
- When updating allocsizes once per argument, skip duplicate key objects so a shared shrink is not applied twice (`Z*STORE`, `SINTER`/`SDIFF`).
- List reads (`LRANGE`/`LINDEX`/`LPOS`) can recompress quicklist nodes on iterator reset; refresh slot allocsizes afterward when `key-memory-histograms` is on.
- Range helpers used by `LPOP COUNT` / `LMPOP` must take the real list key (not `c->argv[1]`), and mutating callers must re-baseline alloc size after the reply before `listElementsRemoved` so recompress is not double-counted.
- Hashtable-encoding regression tests must lower `hash-max-listpack-entries` (default 512) or they never leave listpack.

## Campaign monitor

- Ignore resolved / outdated review threads; ignore human issue comments already answered by a later author comment.
- Split CLA check failures (`cla-signed`, CLA Assistant) into `cla_blocking` so they are not confused with project CI.
- Treat `gh pr view` fetch failures as `monitor_error` (not `human_comment`) so transient auth blips are not mistaken for maintainer feedback.
- On rework signals, append a rule-log entry via `oocoder campaign learn` before relying on chat memory.
- Daily `maxPrsPerDay` uses the rate counter only — never candidate `updatedAt` (sync/monitor bumps that and would false-cap Linux watch waves).

## delta-io/delta

- Mark Databricks Runtime-only options (e.g. `withEventTimeOrder`) explicitly so OSS docs do not imply an OSS API.

## Campaign ops

```bash
pnpm --filter @oocoder/cli exec node ./dist/bin.js campaign status
pnpm --filter @oocoder/cli exec node ./dist/bin.js campaign sync
pnpm --filter @oocoder/cli exec node ./dist/bin.js campaign scrub
pnpm --filter @oocoder/cli exec node ./dist/bin.js campaign monitor
pnpm --filter @oocoder/cli exec node ./dist/bin.js campaign learn --repo owner/name --source '#N' --title '…' --rule '…' --encode
pnpm --filter @oocoder/cli exec node ./dist/bin.js campaign run -n 10
pnpm --filter @oocoder/cli exec node ./dist/bin.js campaign watch --interval 300 --max-ticks 0
```

Target: `campaign.targetPrs: 150` in `configs/policy.yaml` / `~/.oocoder/policy.yaml`.

24×7 on Linux: [CAMPAIGN_LINUX_24X7.md](CAMPAIGN_LINUX_24X7.md) (`deploy/systemd/oocoder-campaign.timer`).
