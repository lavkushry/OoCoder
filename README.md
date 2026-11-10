# oss-agent

Self-hosted autonomous agent that contributes to open-source projects like a careful human. **The agent prepares; you approve every outward action.**

Operational model combines:

- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)-inspired **sandbox engine** (session lifecycle, file/command APIs, credential vault, egress policy)
- [Khoj](https://github.com/khoj-ai/khoj) + [last30days](https://github.com/mvanhorn/last30days-skill)-inspired **project research** before every action (local corpus + engagement-scored signals)
- [n8n](https://github.com/n8n-io/n8n)-inspired **workflow engine** (nodes, connections, IF/wait, item data) driving the contribution graph
- [Paperclip](https://github.com/paperclipai/paperclip)-style **pre-contribution analysis** (goal alignment, budget, governance, readiness) before any coding work
- [SuperPlane](https://github.com/superplanehq/superplane)’s **software factory** board: **Backlog → Running → Review → Done**
- [Strix](https://github.com/usestrix/strix)-inspired **code quality / security analysis** (severity + SARIF) on the contribution diff before PR draft

Default workflow main path: `project_research → analyze → await_analysis_approval → intake → reproduce → implement → verify → quality_analysis → await_approval` (research also re-runs before each step and before gateway enqueue).

Spec: [docs/PRD_AND_DESIGN.md](docs/PRD_AND_DESIGN.md) · Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Runbook: [docs/OPERATOR_RUNBOOK.md](docs/OPERATOR_RUNBOOK.md)

## Requirements

- Python 3.12+
- Linux recommended for rootless Podman; default OpenSandbox local runtime uses Fake isolation offline (`prefer_podman=True` when Podman is available)
- Optional: GitHub App credentials (gateway only)

## Quick start

```bash
make setup
make test
make lint
make demo    # offline end-to-end on fixtures (no outward network writes)
make eval    # writes docs/EVAL_REPORT.md
```

Factory board + approval queue:

```bash
oss-agent factory board
oss-agent factory close <work_order_id> --result completed
oss-agent queue
oss-agent approve <draft_id> --by sponsor
oss-agent kill-switch --enable
```

Local UI (localhost only) — factory board + queue:

```bash
make run
# open http://127.0.0.1:8787/
```

## Safety (enforced in code)

1. Only `gateway/` holds GitHub write credentials
2. Outward actions are drafts; default requires approval
3. Per-project rate limits in the gateway
4. AI disclosure injected by the gateway
5. Banned/unclear AI policy → refuse
6. Repo content is untrusted data
7. Sandbox never sees tokens/keys; default-deny network
8. Evidence from sandbox capture only
9. Global kill switch
10. Append-only audit log

## Layout

See [AGENTS.md](AGENTS.md). Legacy TypeScript OoCoder remains under `packages/` and is not the Definition of Done target.
