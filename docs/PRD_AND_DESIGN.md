# OSS Contribution Agent: PRD and AI System Design

Version 2, updated for a self-hosted Linux build. Covers the product requirements first, then the system design.

## Part 1: Product Requirements (PRD)

### 1. Summary

An agent that behaves like a careful human open-source contributor: it reads the project's rules, finds suitable issues, reproduces problems in a sandbox, drafts small focused patches with tests, and prepares pull requests. A human sponsor approves every outward action and owns the result. It runs on a Linux machine you control.

### 2. Problem

Contributing to a large OSS project (Kubernetes is the strictest example) takes hours of unglamorous groundwork: learning the rules, finding an issue nobody has claimed, reproducing it, matching code style, passing CI, and handling slow review cycles. Most of that groundwork is automatable. The judgment (is this change wanted, who should review it) is not, and naive automation has already led projects to ban AI-generated PRs.

### 3. Product Principle

**The agent prepares, the human decides.** Fewer, better contributions beat volume. If a design choice trades maintainer trust for throughput, trust wins.

### 4. Goals

- Cut the time from "found an issue" to "reviewable PR draft" to under a few hours of mostly unattended work
- Produce PRs that follow each project's rules exactly (template, DCO/CLA, release notes, labels, AI disclosure)
- Reach a merge rate comparable to a competent new human contributor
- Keep a full audit trail of every decision the agent makes

### 5. Non-Goals

- Fully autonomous submission with no human approval
- Contributing to projects that ban AI-assisted contributions
- Mass contribution across many projects at once
- Large features or architectural changes in v1 (docs, small bug fixes, tests, and lint-level fixes only)
- Impersonating a human or hiding that an AI was involved

### 6. Users

| User | Need |
| --- | --- |
| Sponsor (you) | Review queue with diffs and reasoning, one-click approve or reject, kill switch |
| Maintainers (indirect) | Small, correct, well-described PRs with clear AI disclosure and a responsive human behind them |

### 7. Key User Stories

- As a sponsor, I point the agent at one project and it tells me whether the project's policy allows AI-assisted contributions.
- As a sponsor, I get a ranked shortlist of candidate issues with a reason for each score.
- As a sponsor, I approve a claim comment before the agent posts it.
- As a sponsor, I see the failing reproduction, the patch, and real test output before approving a PR.
- As a sponsor, I get review comments classified (nit, bug, design, question) with drafted replies I can edit.
- As a sponsor, I can halt all outbound activity instantly.

### 8. Functional Requirements

**FR1: Rules ingestion.** Parse CONTRIBUTING, CODE\_OF\_CONDUCT, PR template, OWNERS/CODEOWNERS, CLA/DCO and any AI policy into a structured rule set. Refuse to proceed if the AI policy forbids it or is unclear.

**FR2: Project memory.** Maintain a per-project index: repo map, ownership map, recent merged and rejected PRs, common reviewer objections, commit and PR style.

**FR3: Issue scouting.** Score issues on tractability, freshness, assignment status, linked PRs, duplicate risk, and required context size. Output a ranked list.

**FR4: Reproduction.** In a sandbox, build the project at the latest main, run the baseline tests, and produce a failing test or script for the issue before any fix.

**FR5: Patch drafting.** Produce a minimal diff matching existing style, with tests and doc updates. No unrelated refactors.

**FR6: Local verification.** Run the project's linters, formatters and test targets. Capture real output as evidence. A PR cannot be drafted without it.

**FR7: PR drafting.** Fill the PR template exactly, link the issue, add required labels or release-note blocks, and include AI disclosure.

**FR8: Review loop.** Ingest CI results and review comments, classify them, distinguish flaky failures from regressions, draft responses and follow-up commits.

**FR9: Human gate.** Every outward action (comment, claim, PR, reply, push) goes through an approval queue. Low-risk actions may be auto-approved only after an explicit per-action opt-in.

**FR10: Audit log.** Record every prompt, tool call, decision, and outward action.

### 9. Non-Functional Requirements

- **Safety:** sandbox has no access to the sponsor's credentials or the host filesystem
- **Rate limits:** hard caps on open PRs and comments per project per day, enforced in code
- **Reliability:** all state is durable, so a crash or reboot resumes exactly where it left off
- **Cost control:** per-task token and time budgets, with a hard stop
- **Transparency:** every PR carries clear AI-assistance disclosure
- **Privacy:** self-hosted state, and only the minimum repo context sent to the model API

### 10. Success Metrics

| Metric | Target for v1 |
| --- | --- |
| PR merge rate | Above 60% of submitted PRs |
| Review rounds per PR | 2 or fewer on average |
| Human approval rate of drafts | Above 70% (low means issue selection is poor) |
| Rule violations in submitted PRs | 0 |
| Maintainer complaints | 0 |
| Time from claim to reviewable draft | Under 4 hours of agent time |

If merge rate falls below target, the agent is costing maintainers time and should be throttled or paused.

### 11. Risks

| Risk | Mitigation |
| --- | --- |
| Spammy or low-quality PRs damage your reputation | Rate limits, human gate, narrow scope |
| Project bans AI contributions | Policy check in the rules engine, hard refusal |
| Agent fabricates test results | Evidence captured from real sandbox runs only |
| Prompt injection from issue or PR text | Treat all repo content as untrusted data, sandbox isolation, no secrets in the sandbox |
| Long review latency breaks the workflow | Durable state machine with wake-up triggers |
| Flaky CI wastes cycles | Flake database plus investigate-before-retry rule |

### 12. Milestones

| Phase | Scope | Exit criterion |
| --- | --- | --- |
| M0 | Read-only scout and summaries for one small project | Shortlist quality judged good by you |
| M1 | Sandbox reproduction and local patching | Correct reproduction on most chosen issues |
| M2 | Full state machine plus human gate, PR drafting | First PR submitted by you with agent drafting |
| M3 | Review-response loop | PR merged with agent-assisted replies |
| M4 | Second project, metrics dashboard | Stable merge rate over 10 or more PRs |

## Part 2: AI System Design

### 1. Design Principles

- **The LLM proposes, code enforces.** All policy, rate limits, and state transitions live in deterministic code.
- **Everything outward goes through a gate.** No component other than the gate holds write credentials.
- **Repo content is untrusted input.** Issues, PRs, and code comments can contain injected instructions.
- **Evidence over claims.** Test results and diffs come from tool output, never from model text.

### 2. Architecture Overview

```
                 +---------------------------+
                 |   Sponsor UI / CLI / TUI  |
                 |  approvals, kill switch   |
                 +-------------+-------------+
                               |
+------------------+   +-------v--------+   +------------------+
| GitHub App       |<->| Orchestrator   |<->| State store      |
| webhooks + API   |   | state machine  |   | Postgres/SQLite  |
| (scoped, gated)  |   | + scheduler    |   | + audit log      |
+------------------+   +---+--------+---+   +------------------+
                           |        |
              +------------+        +-------------+
              |                                   |
     +--------v---------+               +---------v----------+
     | Agent workers    |               | Rules engine       |
     | (LLM + tools)    |               | policy, limits     |
     +--------+---------+               +--------------------+
              |
     +--------v---------+      +-----------------------+
     | Sandbox runner   |      | Project memory        |
     | containers/microVM|     | vector + structured   |
     +------------------+      +-----------------------+
```

### 3. Components

**Orchestrator.** A durable state machine with a scheduler. It owns all transitions, timeouts, and retries. It wakes tasks on events (webhook, timer, human decision). The LLM never decides state transitions on its own; it returns a proposed next action that the orchestrator validates.

**Agent workers.** Stateless LLM loops with a fixed tool list per state. Each state gets only the tools it needs (for example, the scouting state has read-only GitHub tools and no sandbox).

**Rules engine.** Deterministic checks: AI policy allowed, CLA/DCO satisfied, PR template complete, rate limits not exceeded, labels valid. Failing any check blocks the action regardless of what the model says.

**Project memory.** Structured data (owners map, label taxonomy, templates, flake list) in SQL, plus a vector index of past PRs, review comments, and docs for retrieval. Retrieval is narrow and per-task, never "load the repo".

**Sandbox runner.** Executes build, test, and patch steps in isolation. Details in section 6.

**GitHub gateway.** The only component that can write to GitHub. It accepts only actions that carry a human approval token and passed the rules engine.

**Sponsor UI.** Start with a CLI or terminal UI and a simple local web page for the approval queue. Do not build a polished app before M2.

### 4. State Machine

```
SCOUTING -> SHORTLISTED -> AWAITING_CLAIM_APPROVAL -> CLAIMED
   -> REPRODUCING -> DESIGNING -> IMPLEMENTING -> SELF_REVIEW
   -> AWAITING_PR_APPROVAL -> SUBMITTED -> IN_REVIEW
   -> (CHANGES_REQUESTED -> IMPLEMENTING) -> APPROVED -> MERGED

Any state -> ABANDONED (timeout, maintainer says no, policy change)
Any state -> PAUSED (kill switch)
```

Every state defines: allowed tools, entry conditions, exit conditions, a timeout, and a token budget. Claiming and PR submission always pass through an approval state.

### 5. Agent Loop Per State

| State | Inputs | Tools | Output |
| --- | --- | --- | --- |
| SCOUTING | project rules, issue list | read-only GitHub search, memory | ranked shortlist with reasons |
| REPRODUCING | chosen issue, repo at main | sandbox shell, file read | failing test or script, baseline results |
| DESIGNING | repro, ownership map | memory, read-only repo search | short design note, flagged if non-trivial |
| IMPLEMENTING | design note | sandbox shell, file edit | minimal diff plus tests |
| SELF\_REVIEW | diff, project style notes | linters, test runner | pass or fail evidence, critique |
| REVIEW LOOP | review comments, CI logs | read-only GitHub, sandbox | classified comments, draft replies, new patch |

Design note: anything beyond a small fix triggers an escalation to the sponsor instead of continuing.

### 6. Sandbox Design on Linux

Since you control the Linux environment, use layered isolation:

- **Baseline:** rootless containers (Podman or Docker) per task, with a read-only base image, non-root user, dropped capabilities, seccomp profile, CPU, memory, and time limits
- **Network:** default deny. Allowlist only the git remote, package registries the project needs, and nothing else. Use a proxy so egress is logged.
- **Stronger option:** for untrusted or complex builds, run tasks inside KVM microVMs (libvirt or Firecracker). Slower to start, much stronger isolation.
- **No secrets inside:** the sandbox never sees GitHub tokens, SSH keys, or model API keys. It receives the repo and returns a patch and logs.
- **Caching:** keep per-project dependency and build caches on a separate volume so builds stay fast without sharing state between tasks.
- **Cleanup:** every task container is ephemeral and destroyed on completion, with logs exported to the audit store.

### 7. Model Strategy

- **Main coding model:** a strong tool-calling model for reproduction, patching, and review replies
- **Cheap model:** triage, classification of review comments, issue scoring first pass
- **Prompt structure:** project rules and style notes in a stable system block, task data in a clearly delimited untrusted block
- **Context discipline:** retrieve narrowly using the ownership map and code search, and cap context per call
- **Optional later:** a local open-weight model for the cheap triage tier if you want fully offline classification

### 8. Data Model (Core Tables)

| Table | Key fields |
| --- | --- |
| projects | id, repo, policy\_json, ai\_allowed, rate\_limits |
| issues | id, project\_id, score, score\_reasons, status |
| tasks | id, issue\_id, state, budget\_tokens, started\_at, deadline |
| artifacts | id, task\_id, type (repro, patch, test\_log, design\_note), path, hash |
| actions | id, task\_id, kind (comment, claim, pr, reply), draft, status, approved\_by, approved\_at |
| review\_items | id, task\_id, source\_comment, class, draft\_reply, status |
| flakes | id, project\_id, test\_name, evidence |
| audit\_log | id, task\_id, ts, actor, event, payload |

### 9. Security and Guardrails (All Enforced in Code)

- Rate caps: open PRs per project, comments per day, retries per CI job
- Mandatory AI disclosure line in PR body and relevant comments
- No self-approve, no `/lgtm`, no `/approve`, no bypass of branch protection
- Refuse projects with banned or unclear AI policy
- Prompt-injection defense: repo content is wrapped as data, sandbox output cannot trigger outward actions without the gate, and the gateway ignores any instruction that did not originate from the orchestrator
- GitHub App with minimum permissions, separate from your personal account
- Global kill switch (a single flag the gateway checks before every call)
- Token and wall-clock budgets per task with hard termination

### 10. Observability and Evaluation

- Structured logs for every prompt, tool call, and decision, stored locally
- Metrics: shortlist precision, reproduction success rate, draft approval rate, review rounds, merge rate, cost per merged PR
- **Offline eval set:** build a set of past closed issues with known fixes, and replay the agent against them before touching a live project
- Weekly review of rejected drafts and maintainer pushback to update the style notes and scoring

### 11. Deployment on Your Linux Environment

- Run the orchestrator and gateway as systemd services under a dedicated non-privileged user
- SQLite for the first milestone, move to Postgres when you need concurrency
- Local web UI bound to localhost, reach it by SSH tunnel
- Webhooks: use a small tunnel or polling at first, so you do not expose the machine publicly
- Secrets in a root-owned file or a local secret store, readable only by the gateway process
- Nightly backup of the state database and audit log

### 12. Suggested Tech Stack

| Layer | Choice |
| --- | --- |
| Language | Python for orchestration and agents (or Go if you prefer a single binary) |
| Orchestration | Hand-rolled state machine or LangGraph, durable via the database |
| Sandbox | Rootless Podman, with KVM microVMs as the upgrade |
| GitHub | GitHub App, using the REST and GraphQL APIs |
| Storage | SQLite then Postgres, plus a vector index such as pgvector or Qdrant |
| UI | CLI first, then a small local web app |
| Observability | Structured JSON logs, plus a simple dashboard |

### 13. Build Order

1. Read-only scout on one small, AI-friendly project, with summaries only
2. Sandbox runner that reproduces one known issue end to end
3. State machine with persistence and the approval queue
4. Patch drafting plus local verification with evidence capture
5. PR drafting with the rules engine blocking bad drafts
6. Review-response loop, then the metrics dashboard

### 14. Open Decisions for You

- Which first project? It should be small, active, with a clear AI policy and responsive maintainers.
- Which model provider and budget per merged PR are acceptable?
- Do you want the sandbox to start with containers or go straight to microVMs?
- Is this a personal tool, or do you plan to open source or productize the agent itself?

### 15. Honest Assessment

The hard parts are issue selection and review handling, not code generation. Expect to spend more time on the scoring, the rules engine, and the evaluation set than on the patching loop. A good v1 is a disciplined assistant that makes you a faster and more careful contributor, and it will beat an unsupervised bot on every metric that matters to maintainers.
