"""Offline contribution-loop fixtures: discussion text, CI plan, no network writes."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from oss_agent.protocols import IssueCandidate
from oss_agent.review.classify import detect_flake
from oss_agent.review.loop import ReviewHandling, handle_review_thread

_FAILURES = frozenset({"failure", "timed_out", "error"})


@dataclass(frozen=True)
class PatchSpec:
    module_relpath: str
    old: str
    new: str
    title: str


@dataclass(frozen=True)
class CiRound:
    sha: str
    conclusion: str
    name: str
    log: str


@dataclass(frozen=True)
class SponsorDecisions:
    """Explicit sponsor decisions from a fixture. Never a live GitHub write."""

    approved_by: str
    approve_discussion: bool
    approve_pr: bool
    approve_follow_up: bool
    approve_replies: bool
    approve_merge: bool
    accept_design: bool


@dataclass(frozen=True)
class ContributionFixture:
    repo: str
    allowlist: tuple[str, ...]
    initial_fix: PatchSpec
    follow_up_fix: PatchSpec | None
    review_comments: tuple[str, ...]
    known_flakes: tuple[str, ...]
    ci_rounds: tuple[CiRound, ...]
    sponsor: SponsorDecisions


@dataclass(frozen=True)
class IterationPlan:
    repair: bool
    reason: str
    flake_detected: bool
    ready_to_merge: bool
    attempts_used: int
    review: ReviewHandling


def build_discussion_comment(issue: IssueCandidate) -> str:
    """Draft an issue comment. The gateway injects AI disclosure later."""
    title = " ".join(issue.title.split())[:200]
    return (
        f"Thanks for opening issue #{issue.number}. "
        "I'm looking into it and will propose a fix if it fits the project's "
        "contribution guidelines.\n\n"
        f"Issue title (untrusted data): {title}"
    )


def load_contribution_fixture(path: Path) -> ContributionFixture:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    data = _as_object(parsed, "fixture")
    follow_raw = data.get("follow_up_fix")
    follow_up = None if follow_raw is None else _patch(_as_object(follow_raw, "follow_up_fix"))
    return ContributionFixture(
        repo=_str(data, "repo"),
        allowlist=tuple(_str_list(data.get("allowlist"), "allowlist")),
        initial_fix=_patch(_as_object(data.get("initial_fix"), "initial_fix")),
        follow_up_fix=follow_up,
        review_comments=tuple(_str_list(data.get("review_comments"), "review_comments")),
        known_flakes=tuple(_str_list(data.get("known_flakes"), "known_flakes")),
        ci_rounds=tuple(_rounds(data.get("ci_rounds"))),
        sponsor=_sponsor(_as_object(data.get("sponsor"), "sponsor")),
    )


def plan_review_iteration(
    *,
    comments: Sequence[str],
    ci_rounds: Sequence[CiRound],
    known_flakes: Sequence[str],
    max_ci_retries: int,
    accept_design: bool,
) -> IterationPlan:
    """
    Decide whether a fixture CI thread needs one sandbox repair.

    A flake is not retried. A real failure consumes one attempt, up to
    ``max_ci_retries``. Merge is ready only after a green round, and only when
    a design comment was explicitly accepted by the sponsor.
    """
    first_log = ci_rounds[0].log if ci_rounds else ""
    review = handle_review_thread(
        list(comments),
        ci_log=first_log,
        known_flakes=list(known_flakes),
    )
    if review.escalate and not accept_design:
        return IterationPlan(
            repair=False,
            reason="design_escalated",
            flake_detected=review.flake_detected,
            ready_to_merge=False,
            attempts_used=0,
            review=review,
        )

    attempts = 0
    repair = False
    saw_success = False
    reason = "no_ci"
    for rnd in ci_rounds:
        if rnd.conclusion in {"pending", "queued", "in_progress"}:
            reason = "pending"
            break
        if rnd.conclusion == "success":
            saw_success = True
            reason = "repaired" if repair else "ci_green"
            break
        if rnd.conclusion in _FAILURES:
            if detect_flake(rnd.log, list(known_flakes)):
                reason = "flake_no_retry"
                repair = False
                break
            if attempts >= max_ci_retries:
                reason = "attempt_cap"
                repair = False
                break
            attempts += 1
            repair = True
            reason = "repair_real_failure"
            continue
        reason = "unknown_conclusion"
        repair = False
        break

    ready = saw_success and reason in {"repaired", "ci_green"}
    if repair and not saw_success:
        ready = False
    return IterationPlan(
        repair=repair and saw_success,
        reason=reason if saw_success or reason != "repair_real_failure" else "repair_unverified",
        flake_detected=review.flake_detected,
        ready_to_merge=ready,
        attempts_used=attempts,
        review=review,
    )


def reply_payloads(
    plan: IterationPlan,
    *,
    issue_number: int,
    research_brief_id: str,
) -> list[dict[str, object]]:
    """Reply drafts. Disclosure is intentionally absent; the gateway adds it."""
    payloads: list[dict[str, object]] = []
    for item in plan.review.items:
        payloads.append(
            {
                "body": str(item["draft_reply"]),
                "issue": issue_number,
                "research_brief_id": research_brief_id,
                "review_class": str(item["class"]),
            }
        )
    return payloads


def _patch(data: Mapping[str, object]) -> PatchSpec:
    return PatchSpec(
        module_relpath=_str(data, "module_relpath"),
        old=_str(data, "old"),
        new=_str(data, "new"),
        title=str(data.get("title") or "follow-up fix"),
    )


def _sponsor(data: Mapping[str, object]) -> SponsorDecisions:
    return SponsorDecisions(
        approved_by=_str(data, "approved_by"),
        approve_discussion=_bool(data, "approve_discussion"),
        approve_pr=_bool(data, "approve_pr"),
        approve_follow_up=_bool(data, "approve_follow_up"),
        approve_replies=_bool(data, "approve_replies"),
        approve_merge=_bool(data, "approve_merge"),
        accept_design=_bool(data, "accept_design"),
    )


def _rounds(value: object) -> list[CiRound]:
    if not isinstance(value, list):
        raise ValueError("ci_rounds")
    rounds: list[CiRound] = []
    for item in value:
        data = _as_object(item, "ci_round")
        rounds.append(
            CiRound(
                sha=_str(data, "sha"),
                conclusion=_str(data, "conclusion"),
                name=_str(data, "name"),
                log=_str(data, "log"),
            )
        )
    return rounds


def _as_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(label)
    out: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(label)
        out[key] = item
    return out


def _str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(key)
    return value


def _bool(data: Mapping[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(key)
    return value


def _str_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(key)
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(key)
        out.append(item)
    return out
