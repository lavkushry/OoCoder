"""Choose one allowlisted issue the contribution loop can reproduce."""

from __future__ import annotations

from collections.abc import Sequence

from oss_agent.learn.apply import skipped_by_lesson
from oss_agent.learn.models import LessonRecord
from oss_agent.protocols import IssueCandidate

_REPRO_LABELS = frozenset({"bug", "test", "tests"})
_SKIP_REASONS = frozenset({"already_assigned", "has_linked_pr", "likely_design_scope"})


class ChoiceError(ValueError):
    """No safe allowlisted issue is available."""


def choose_allowlisted_issue(
    ranked: Sequence[IssueCandidate],
    *,
    allowlisted_repos: set[str],
    repo: str,
    min_score: float,
    lessons: Sequence[LessonRecord] = (),
) -> IssueCandidate:
    """
    Pick the issue the offline loop will work.

    ``ranked`` must already be ordered by ``rank_issues`` (highest score first).
    The repo must be on the allowlist. Assigned issues, linked-PR issues, and
    design-scope issues are skipped. A stand-down lesson for the same title is
    skipped too. When any remaining issue is labeled as a bug or test, that set
    wins so the loop can reproduce a failure. Otherwise the highest-scoring
    eligible issue is used.
    """
    if repo not in allowlisted_repos:
        raise ChoiceError(f"{repo} is not allowlisted")

    eligible = [
        candidate
        for candidate in ranked
        if candidate.score >= min_score
        and _SKIP_REASONS.isdisjoint(candidate.score_reasons)
        and not skipped_by_lesson(candidate.title, candidate.body.body, lessons)
    ]
    if not eligible:
        raise ChoiceError(f"no eligible issue on {repo}")

    reproducible = [
        candidate
        for candidate in eligible
        if any(label in _REPRO_LABELS for label in candidate.labels)
    ]
    return reproducible[0] if reproducible else eligible[0]
