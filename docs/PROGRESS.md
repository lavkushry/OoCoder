# Progress

## Current role

**BUILDER** — offline contribution loop through a fixture merge

Earlier milestones (M0–M4, eval, docs) stay done. This pass extends the existing factory line so one offline run reaches `MERGED` without a live GitHub write.

## STEP 0 — Bootstrap

- [x] AGENTS.md, .cursor/rules/*.mdc, docs/*, Makefile, pyproject.toml, pre-commit, CI, PRD copy

## ARCHITECT

- [x] docs/ARCHITECTURE.md, Protocols, config, DB+Alembic, state machine

## BUILDER / SANDBOX / SECURITY / EVAL / DOCS

| Milestone | Status |
| --- | --- |
| M0 Read-only scout | done |
| M1 Sandbox + reproduction | done |
| M2 State machine + gateway | done |
| M3 Patch + PR drafting | done |
| M4 Review loop | done |
| Eval harness + report | done |
| README + runbook + systemd | done |

### Final gate command output

```
$ make lint && make test && make demo && make eval
.venv/bin/ruff check src tests
All checks passed!
.venv/bin/ruff format --check src tests
50 files already formatted
.venv/bin/mypy src/oss_agent
Success: no issues found in 40 source files
.venv/bin/pytest -q
...................................                                      [100%]
35 passed in 8.50s
.venv/bin/python -m oss_agent.cli.main demo --offline
=== oss-agent offline demo ===
shortlist_top_issue=#1
reproduced=True
verify_ok=True
pr_draft_id=act_330a9d13bea94575
pr_errors=[]
review_classes=['nit', 'flake', 'design', 'flake']
flake_detected=True
demo OK (no outward network writes)
.venv/bin/python -m oss_agent.eval.harness --out docs/EVAL_REPORT.md
{'shortlist_precision_at_2': 1.0, 'policy_ban_detected': 1.0, 'reproduction_success': 1.0, 'draft_quality': 1.0, 'flake_handling': 1.0}
```

### Invariant tests

`tests/test_invariants.py` + `tests/test_invariant_imports.py` cover invariants 1–10 by name (`test_inv1`…`test_inv10`).

## Factory extensions

- [x] SuperPlane-style factory board + `oss-contribution` line
- [x] Paperclip-inspired `analyze` / `await_analysis_approval`
- [x] Strix-inspired `quality_analysis` (severity + SARIF) before PR draft
- [x] n8n-inspired `workflow/` engine driving OSS contribution graph (IF + wait)
- [x] Khoj + last30days-inspired `research/` before every action + gateway gate
- [x] OpenSandbox-inspired sandbox engine (sessions, vault, egress; default backend)

```
$ make lint && make test && make demo
Success: no issues found in 65 source files
64 passed in 18.05s
demo OK — workflow success; work order in Review lane
```

## Offline loop through fixture merge

The factory line now chooses an allowlisted bug, drafts a discussion comment, reproduces, patches from sandbox output, passes the quality gate, opens a PR draft, applies one review/CI follow-up, and closes the work order only after an explicit fixture sponsor approval. Discussion is approved before reproduction so the claim stays in front of sandbox work. AI disclosure is still injected by the gateway on send. `auto_approve` stays off. The merge is a task transition to `MERGED`, not a GitHub merge call.

```
$ make lint && make test && make demo && make eval
.venv/bin/ruff check src tests
All checks passed!
.venv/bin/ruff format --check src tests
85 files already formatted
.venv/bin/mypy src/oss_agent
Success: no issues found in 68 source files
.venv/bin/pytest -q
72 passed in 78.92s
.venv/bin/python -m oss_agent.cli.main demo --offline
chosen_issue=#3
discussion_action_id=act_21808bc5872f4caa
pr_draft_id=act_7534a811af7142c7
merged=True task_state=MERGED
final_state=closed result=completed
demo OK — fixture loop merged after sponsor approval (no live GitHub write)
.venv/bin/python -m oss_agent.eval.harness --out docs/EVAL_REPORT.md
{'shortlist_precision_at_2': 1.0, 'policy_ban_detected': 1.0, 'reproduction_success': 1.0, 'draft_quality': 1.0, 'flake_handling': 1.0, 'fixture_merge': 1.0}
```

## Self-heal and self-learn

The factory and pipeline still choose, discuss, reproduce, implement, quality-gate, draft a PR, and merge only after sponsor approval. A failure captured from the sandbox or a fixture CI log is now diagnosed from that log, repaired, and re-checked in the sandbox. Repair attempts cap at 2. A third real failure stands down with a draft comment; the gateway adds AI disclosure on send. A verified repair is stored in the `lessons` table keyed by repo and failure signature. The next run loads that row before choose, implement, and repair.

```
$ .venv/bin/pytest -q
........................................................................ [ 90%]
........                                                                 [100%]
80 passed in 37.88s
$ make lint && make demo
.venv/bin/ruff check src tests
All checks passed!
.venv/bin/ruff format --check src tests
94 files already formatted
.venv/bin/mypy src/oss_agent
Success: no issues found in 76 source files
.venv/bin/python -m oss_agent.cli.main demo --offline
chosen_issue=#3
merged=True task_state=MERGED
heal_learn run_a_attempts=1 signature=assert:add:delta:+1 run_b_attempts=0 lesson=les_bcbf80d06723
demo OK — fixture loop merged after sponsor approval (no live GitHub write)
demo OK — run B avoided the failure by applying the stored lesson
```

## Monitor until merged (campaign target 150)

`campaign.targetPrs: 150` is the DE campaign cap. This pass does not open those pull requests. The coding brain is Cursor Agent local runtime when a real Cursor credential is present, and Grok only when it is absent. After a PR draft, `monitor_until_merged` walks captured checks and reviews, heals at most twice, then stands down. The gateway injects disclosure on send. Podman argv is the rootless Linux form (`--userns=keep-id`, `--network=none`). This process is macOS, so that command was not executed and a live Linux Cursor Agent run was not started.

```
$ .venv/bin/mypy src/oss_agent
Success: no issues found in 78 source files
$ .venv/bin/pytest -q tests/test_monitor_until_merged.py tests/test_heal_learn.py
.............                                                            [100%]
13 passed in 30.10s
$ .venv/bin/pytest -q
........................................................................ [ 84%]
.............                                                            [100%]
85 passed in 37.89s
```
