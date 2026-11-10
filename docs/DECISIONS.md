# Decisions

Conservative defaults chosen when the mission was ambiguous. One line each.

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-11-10 | Primary deliverable is a new Python 3.12 package `src/oss_agent/`; leave existing TypeScript `packages/` in place as legacy | Avoid deleting working code; DoD targets the Python system in the PRD |
| 2026-11-10 | Copy PRD from root markdown into `docs/PRD_AND_DESIGN.md` | Mission references that path; keep original file as a duplicate source |
| 2026-11-10 | Hand-rolled durable state machine (not LangGraph) | Easier to unit/property-test illegal transitions and timeouts |
| 2026-11-10 | SQLite + SQLAlchemy + Alembic; schema portable to Postgres | PRD says SQLite first; migrations keep portability |
| 2026-11-10 | Sandbox: Podman runner first; `SandboxRunner` Protocol for a future KVM backend | Matches target environment; interface allows upgrade without rewrite |
| 2026-11-10 | Model access behind `ModelProvider` Protocol with stub + OpenAI-compatible HTTP adapter | No vendor lock; unit tests use stub; real keys only at runtime |
| 2026-11-10 | Sponsor UI: FastAPI localhost-only + Typer CLI for approvals | PRD allows CLI first then small local web; both needed for DoD |
| 2026-11-10 | Fixture project: synthetic tiny Python package under `fixtures/projects/` | Offline demo/eval without depending on a live OSS repo |
| 2026-11-10 | Default policy: all outward action types require human approval | Matches non-negotiable invariant 2 |
| 2026-11-10 | GitHub write credentials live only under `src/oss_agent/gateway/` | Invariant 1; static import tests enforce it |
| 2026-11-10 | Escape/reproduction unit tests use `FakeSandboxRunner` enforcing the same deny policies; `PodmanSandboxRunner` used when `podman` is installed | Builder host may lack Podman; policy must still be tested offline |
| 2026-11-10 | Milestone numbering follows the mission (M2=gateway/orchestrator, M3=patch/PR, M4=review), not the coarser PRD phase table | Mission ACs are more specific |
| 2026-11-10 | Adopt SuperPlane Factory domain (factory, lines, work orders, executions, artifacts, events, board lanes) in Python under `src/oss_agent/factory/` rather than vendoring the Go/React monorepo | Reuse oss-agent safety invariants; SuperPlane is Apache-2.0 inspiration, not a drop-in binary |
| 2026-11-10 | Default line `oss-contribution`: analyze→await_analysis_approval→intake→reproduce→implement→verify→quality_analysis→await_approval; successful line leaves order OPEN (Review lane) for human close | Matches SuperPlane “human review after automated checks” |
| 2026-11-10 | Add Paperclip-inspired `analyze` + `await_analysis_approval` before contribution steps | Goal alignment, budget, governance, readiness gate before coding agents run |
| 2026-11-10 | Offline demo auto-approves analysis when recommendation=go (`goal.demo_auto_approve_analysis`) | Keeps `make demo` offline; production leaves the gate for sponsor approval |
| 2026-11-10 | Strix-inspired `quality_analysis` after verify: static secrets/dangerous-API/size/tests gate with SARIF; block at severity≥high by default; do not vendor Strix CLI | Offline deterministic gate; optional `prefer_strix_cli` later via Protocol |
