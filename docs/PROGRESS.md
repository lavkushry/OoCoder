# Progress

## Current role

**DOCS WRITER** complete — Definition of Done met

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
