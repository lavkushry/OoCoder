# oss-agent

Self-hosted autonomous agent that contributes to open-source projects like a careful human contributor. The agent prepares; a human sponsor approves every outward action.

Full product and design spec: [docs/PRD_AND_DESIGN.md](docs/PRD_AND_DESIGN.md). Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Repo layout

```
src/oss_agent/          # Python package (orchestration, gateway, scout, sandbox, UI)
  workflow/             # n8n-inspired workflow engine (nodes, connections, IF/wait)
  research/             # Khoj + last30days-inspired project research (before every action)
  factory/              # SuperPlane-inspired software factory (work orders, lines, board)
  analysis/             # Paperclip-inspired pre-contribution analysis (goal/budget/governance)
  quality/              # Strix-inspired code quality / security gate (severity + SARIF)
  gateway/              # ONLY module that may hold/use GitHub write credentials
  orchestrator/         # Durable state machine
  scout/                # Rules ingestion, project memory, issue ranking
  sandbox/              # OpenSandbox-inspired engine (sessions, vault, Podman/Fake backends)
  rules/                # Deterministic policy / rate limits / AI disclosure
  models/               # LLM provider interface (main + cheap)
  db/                   # Schema, migrations, repositories
  audit/                # Append-only audit log
  cli/                  # Approval queue + factory board CLI
  web/                  # Localhost-only sponsor UI (factory board)
prompts/                # Versioned prompt files (untrusted content in delimited blocks)
fixtures/               # Offline eval and demo fixtures (no network writes)
tests/                  # Unit, property, invariant, escape, and eval tests
deploy/systemd/         # systemd unit files
docs/                   # PRD, architecture, progress, decisions, blockers, runbooks
packages/               # Legacy TypeScript OoCoder (not the DoD target)
```

## Commands

| Target | Purpose |
| --- | --- |
| `make setup` | Create venv, install package + dev deps, install pre-commit hooks |
| `make test` | Run full pytest suite |
| `make lint` | ruff check + format check + mypy strict on core |
| `make run` | Start orchestrator + localhost UI (dev) |
| `make demo` | Offline end-to-end fixture demo (no outward network writes) |
| `make eval` | Run offline eval harness; write report under `docs/` |

## Coding standards

- Python 3.12+, type hints everywhere; mypy strict on `src/oss_agent/` core modules.
- ruff clean (lint + format).
- Every module has unit tests. State machine has property tests for illegal transitions.
- No network calls in unit tests; use recorded fixtures under `fixtures/`.
- Config via a single validated pydantic schema; no magic constants.
- Structured JSON logging. Secrets only via environment or a root-owned file read by the gateway process.
- Prompts live under `prompts/`; untrusted repo content always inside clearly delimited blocks.
- Small commits, one concern each. Update [docs/PROGRESS.md](docs/PROGRESS.md) after every milestone with real command output.

## Runtime invariants (enforced in code, covered by tests)

1. Only the GitHub gateway module holds write credentials. Nothing else imports them.
2. Every outward action is a draft row and is sent only if policy allows it. Default: all outward actions require approval. Auto-approve is per-action-type opt-in, off by default.
3. Rate limits per project (open PRs, comments/day, CI retries) enforced in the gateway.
4. AI-disclosure line is injected into every PR body and comment by the gateway, not the model.
5. Refuse any project whose AI policy is banned or unclear.
6. Repo content is untrusted data: wrapped in prompts; never triggers tool calls or outward actions by itself.
7. Sandbox never receives tokens, SSH keys, or model API keys. Default-deny network with allowlist and logged egress.
8. Test results and diffs in drafts come from captured sandbox output only, never from model text.
9. Global kill switch checked by the gateway before every call.
10. Every prompt, tool call, decision, and outward action is written to an append-only audit log.

## Roles

Work through roles in order; switch role explicitly in `docs/PROGRESS.md`:

1. ARCHITECT → 2. BUILDER (M0–M4) → 3. SANDBOX ENGINEER → 4. SECURITY REVIEWER (after each milestone) → 5. EVAL ENGINEER → 6. DOCS WRITER

## Autonomy contract (for builders of this repo)

- When ambiguous, pick the most conservative option and record it in `docs/DECISIONS.md`.
- After every milestone: full `make test` + `make lint`, fix failures, then continue.
- Never claim something works without pasting real command output into `docs/PROGRESS.md`.
- If blocked >3 attempts: write to `docs/BLOCKERS.md`, stub behind an interface, continue.
- Stop only when Definition of Done is met or remaining items are in `BLOCKERS.md`.
