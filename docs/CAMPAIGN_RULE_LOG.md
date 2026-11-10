# Campaign rule log

Append-only record of new DE-campaign learnings. Newest days at the bottom.

After each entry:

1. Roll the rule into the topic section in `docs/CAMPAIGN_LEARNINGS.md`.
2. When it should steer future patches, encode it in `packages/core/src/implement.ts`.
3. Prefer `oocoder campaign learn` so the audit event is written too.

## 2026-11-10

### trinodb/trino — CLA is email PDF, not cla-assistant.io
- **Source:** trinodb/trino#32057 (cla-signed workflow / `.github/bin/check-cla.js`)
- **Rule:** Trino CLA is on file only after a signed Individual CLA PDF is emailed to `cla@trino.io` (see github.com/trinodb/cla). cla-assistant.io does not apply; approved PRs stay blocked until contributors.json lists the author.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### trinodb/trino — prefer DEVELOPMENT.md over Sphinx develop.md duplicates
- **Source:** trinodb/trino#32057 (@findepi)
- **Rule:** Contributor tooling notes (e.g. Airlift codestyle) belong in `.github/DEVELOPMENT.md`; do not duplicate them into Sphinx `develop.md`.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### redis/redis — LPOP/LMPOP range reply allocsize
- **Source:** redis/redis#16227 (Bugbot / Codex)
- **Rule:** List range helpers must take the real list key (not `c->argv[1]`); mutating callers must re-baseline alloc size after the range reply before `listElementsRemoved` so recompress is not double-counted.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### redis/redis — do not wrap setTypeExpire in updateSlotAllocSize
- **Source:** redis/redis#16226 (Bugbot / Codex)
- **Rule:** `setTypeExpire` / `setTypeExpireIfNeeded` already refresh slot accounting and may free the object; do not wrap them in another `updateSlotAllocSize`.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### redis/redis — hashtable tests must lower listpack threshold
- **Source:** redis/redis#16225 (Bugbot)
- **Rule:** Hashtable-encoding regression tests must lower `hash-max-listpack-entries` (default 512) or they never leave listpack.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### campaign-monitor — CLA vs CI and resolved threads
- **Source:** oocoder campaign monitor noise on duckdb/trino
- **Rule:** Ignore resolved/outdated review threads and human comments already answered by a later author comment; split CLA check failures into `cla_blocking` (not `ci_failing`); mark monitor fetch failures as `monitor_error` not `human_comment`.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` (campaign monitor section)

### campaign-monitor — monitor_error vs human_comment
- **Source:** monitor fetch 401
- **Rule:** Treat gh pr view fetch failures as monitor_error attention, not human_comment, so transient 401s are not mistaken for maintainer feedback.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### trinodb/trino — CLA PDF emailed; await contributors.json
- **Source:** cla@trino.io email
- **Rule:** After emailing the signed Individual CLA PDF to cla@trino.io, wait for trinodb/cla contributors to list the GitHub login (often a few days). Do not treat cla-signed green as instant; approved PRs stay blocked until then.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### campaign-ops — Daily PR cap uses rate counter only
- **Source:** status Today vs rate counter
- **Rule:** Do not count candidate updatedAt (bumped by sync/monitor) toward maxPrsPerDay; use getRateCount(prs) so continuous Linux watch waves are not false-capped.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### campaign-ops — Surgical gh PRs when model key missing
- **Source:** wave to 150 without CURSOR_API_KEY
- **Rule:** When CURSOR_API_KEY/GROK_API_KEY are unset, campaign run fail-fasts; continue the wave with surgical gh docs/code PRs and campaign sync so Linux watch can still monitor toward targetPrs.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### duckdb/duckdb-web — bundle docs; do not spray settings
- **Source:** duckdb/duckdb-web#7801 (@szarnyasg)
- **Rule:** Bundle related DuckDB doc edits into one PR. Do not open one setting per PR. Maintainers close unsolicited "Document X" setting PRs (no issue, not requested) because they burn CI.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### duckdb/duckdb-web — TPC-H dbgen warning is obsolete in 2.0
- **Source:** duckdb/duckdb-web#7726 (@szarnyasg)
- **Rule:** Do not rewrite the TPC-H dbgen compliance warning. It no longer applies from DuckDB 2.0.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`

### duckdb/duckdb-web — do not reopen closed agent docs
- **Source:** duckdb/duckdb-web#7712 (@szarnyasg)
- **Rule:** If a DuckDB maintainer closes a pull request, leave it closed. Do not send another spray of agent-written docs for review.
- **Encode:** update `docs/CAMPAIGN_LEARNINGS.md` and `packages/core/src/implement.ts`
