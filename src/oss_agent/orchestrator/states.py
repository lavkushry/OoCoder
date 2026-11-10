"""State machine: states, allowed tools, transitions, entry/exit conditions."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class TaskState(StrEnum):
    SCOUTING = "SCOUTING"
    SHORTLISTED = "SHORTLISTED"
    AWAITING_CLAIM_APPROVAL = "AWAITING_CLAIM_APPROVAL"
    CLAIMED = "CLAIMED"
    REPRODUCING = "REPRODUCING"
    DESIGNING = "DESIGNING"
    IMPLEMENTING = "IMPLEMENTING"
    SELF_REVIEW = "SELF_REVIEW"
    AWAITING_PR_APPROVAL = "AWAITING_PR_APPROVAL"
    SUBMITTED = "SUBMITTED"
    IN_REVIEW = "IN_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    MERGED = "MERGED"
    ABANDONED = "ABANDONED"
    PAUSED = "PAUSED"


# Tools the worker may call while in each state. Empty => no tools.
ALLOWED_TOOLS: Final[dict[TaskState, frozenset[str]]] = {
    TaskState.SCOUTING: frozenset(
        {"github_search_issues", "github_get_file", "memory_query", "memory_upsert"}
    ),
    TaskState.SHORTLISTED: frozenset(),
    TaskState.AWAITING_CLAIM_APPROVAL: frozenset(),
    TaskState.CLAIMED: frozenset({"github_get_issue"}),
    TaskState.REPRODUCING: frozenset(
        {"sandbox_shell", "sandbox_read_file", "sandbox_write_file", "memory_query"}
    ),
    TaskState.DESIGNING: frozenset({"memory_query", "repo_search", "sandbox_read_file"}),
    TaskState.IMPLEMENTING: frozenset(
        {"sandbox_shell", "sandbox_read_file", "sandbox_write_file", "memory_query"}
    ),
    TaskState.SELF_REVIEW: frozenset({"sandbox_shell", "sandbox_read_file", "memory_query"}),
    TaskState.AWAITING_PR_APPROVAL: frozenset(),
    TaskState.SUBMITTED: frozenset({"github_get_pr"}),
    TaskState.IN_REVIEW: frozenset(
        {
            "github_list_comments",
            "github_get_checks",
            "memory_query",
            "sandbox_shell",
            "sandbox_read_file",
            "sandbox_write_file",
        }
    ),
    TaskState.CHANGES_REQUESTED: frozenset(
        {
            "sandbox_shell",
            "sandbox_read_file",
            "sandbox_write_file",
            "memory_query",
            "github_list_comments",
        }
    ),
    TaskState.APPROVED: frozenset(),
    TaskState.MERGED: frozenset(),
    TaskState.ABANDONED: frozenset(),
    TaskState.PAUSED: frozenset(),
}

ENTRY_CONDITIONS: Final[dict[TaskState, str]] = {
    TaskState.SCOUTING: "Project exists, ai_allowed is true, kill_switch is false",
    TaskState.SHORTLISTED: "At least one scored issue persisted with reasons",
    TaskState.AWAITING_CLAIM_APPROVAL: "Sponsor selected an issue; claim draft created",
    TaskState.CLAIMED: "Claim draft approved and gateway send succeeded (or dry-run recorded)",
    TaskState.REPRODUCING: "Repo clone available to sandbox; issue body wrapped as UntrustedText",
    TaskState.DESIGNING: "Reproduction artifact with failing test evidence exists",
    TaskState.IMPLEMENTING: "Design note accepted (or auto for docs/lint/tests scope)",
    TaskState.SELF_REVIEW: "Patch artifact from sandbox diff exists",
    TaskState.AWAITING_PR_APPROVAL: "Self-review passed with sandbox lint/test evidence",
    TaskState.SUBMITTED: "PR draft approved and gateway send succeeded",
    TaskState.IN_REVIEW: "Remote PR exists; waiting on CI or reviewers",
    TaskState.CHANGES_REQUESTED: "Review item classified as requiring code change",
    TaskState.APPROVED: "Maintainer approval detected (informational; no self-approve)",
    TaskState.MERGED: "PR merged on remote",
    TaskState.ABANDONED: "Timeout, policy change, or sponsor/maintainer rejection",
    TaskState.PAUSED: "Kill switch engaged or sponsor paused the task",
}

EXIT_CONDITIONS: Final[dict[TaskState, str]] = {
    TaskState.SCOUTING: "Ranked shortlist written to issues table",
    TaskState.SHORTLISTED: "Sponsor picks an issue or abandons",
    TaskState.AWAITING_CLAIM_APPROVAL: "Claim approved/rejected",
    TaskState.CLAIMED: "Ready to reproduce",
    TaskState.REPRODUCING: "Failing repro artifact stored; baseline log stored",
    TaskState.DESIGNING: "Design note stored; escalate if non-trivial",
    TaskState.IMPLEMENTING: "Minimal diff + tests captured from sandbox",
    TaskState.SELF_REVIEW: "Lint/test evidence pass or fail recorded",
    TaskState.AWAITING_PR_APPROVAL: "PR approved/rejected by sponsor",
    TaskState.SUBMITTED: "Webhook/poll moves to IN_REVIEW",
    TaskState.IN_REVIEW: "CI+comments classified; maybe CHANGES_REQUESTED or APPROVED",
    TaskState.CHANGES_REQUESTED: "Follow-up patch evidence ready for re-review path",
    TaskState.APPROVED: "Merged or abandoned",
    TaskState.MERGED: "Terminal",
    TaskState.ABANDONED: "Terminal",
    TaskState.PAUSED: "Kill switch cleared and sponsor resumes",
}

# Legal directed edges. ABANDONED and PAUSED are reachable from any non-terminal.
_FORWARD: Final[dict[TaskState, frozenset[TaskState]]] = {
    TaskState.SCOUTING: frozenset({TaskState.SHORTLISTED}),
    TaskState.SHORTLISTED: frozenset({TaskState.AWAITING_CLAIM_APPROVAL}),
    TaskState.AWAITING_CLAIM_APPROVAL: frozenset({TaskState.CLAIMED}),
    TaskState.CLAIMED: frozenset({TaskState.REPRODUCING}),
    TaskState.REPRODUCING: frozenset({TaskState.DESIGNING}),
    TaskState.DESIGNING: frozenset({TaskState.IMPLEMENTING}),
    TaskState.IMPLEMENTING: frozenset({TaskState.SELF_REVIEW}),
    TaskState.SELF_REVIEW: frozenset({TaskState.AWAITING_PR_APPROVAL, TaskState.IMPLEMENTING}),
    TaskState.AWAITING_PR_APPROVAL: frozenset({TaskState.SUBMITTED}),
    TaskState.SUBMITTED: frozenset({TaskState.IN_REVIEW}),
    TaskState.IN_REVIEW: frozenset(
        {TaskState.CHANGES_REQUESTED, TaskState.APPROVED, TaskState.MERGED}
    ),
    TaskState.CHANGES_REQUESTED: frozenset({TaskState.IMPLEMENTING}),
    TaskState.APPROVED: frozenset({TaskState.MERGED}),
    TaskState.MERGED: frozenset(),
    TaskState.ABANDONED: frozenset(),
    TaskState.PAUSED: frozenset(),  # resume restores prior state via orchestrator, not an edge
}

_TERMINAL: Final[frozenset[TaskState]] = frozenset({TaskState.MERGED, TaskState.ABANDONED})


class TransitionTable:
    """Deterministic transition validator. LLM proposals are checked here."""

    @staticmethod
    def allowed_tools(state: TaskState) -> frozenset[str]:
        return ALLOWED_TOOLS[state]

    @staticmethod
    def can_transition(src: TaskState, dst: TaskState) -> bool:
        if src in _TERMINAL:
            return False
        if dst == TaskState.ABANDONED and src not in _TERMINAL:
            return True
        if dst == TaskState.PAUSED and src not in _TERMINAL and src != TaskState.PAUSED:
            return True
        return dst in _FORWARD.get(src, frozenset())

    @staticmethod
    def legal_targets(src: TaskState) -> frozenset[TaskState]:
        if src in _TERMINAL:
            return frozenset()
        targets = set(_FORWARD.get(src, frozenset()))
        if src != TaskState.PAUSED:
            targets.add(TaskState.ABANDONED)
            targets.add(TaskState.PAUSED)
        return frozenset(targets)

    @staticmethod
    def assert_transition(src: TaskState, dst: TaskState) -> None:
        if not TransitionTable.can_transition(src, dst):
            msg = f"illegal transition {src} -> {dst}"
            raise ValueError(msg)
