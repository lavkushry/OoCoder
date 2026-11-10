# Adding a new project

1. Confirm the project's AI policy is **allowed** (not banned, not unclear).
2. Clone or point the agent at the repo path / GitHub `owner/repo`.
3. Run rules ingestion (`ingest_project_rules`) — the agent refuses banned/unclear.
4. Set per-project rate limits in config / `projects.rate_limits`.
5. Keep auto-approve off until you trust drafts for that repo.
6. Add the repo to any allowlist you maintain operationally.
7. Run a scout-only pass first (`SCOUTING` → shortlist) before enabling claim/PR.

Never feed GitHub write credentials into the sandbox or worker processes.
