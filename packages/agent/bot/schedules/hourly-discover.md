---
cron: "0 * * * *"
---

Run an OoCoder discovery pass for autonomous OSS contribution.

1. Call discover_issues with a small limit (5–10).
2. Summarize the top candidates that appear allowlisted / actionable.
3. For at most one high-confidence candidate, call start_contribution with dryRun=true first.
4. If dry-run looks sane and the repo is allowlisted, call start_contribution without dryRun (local runtime).
5. Never exceed policy rate limits. Always keep AI disclosure on comments and PRs.
6. End with a short status report of what was discovered and what (if anything) was shipped.
