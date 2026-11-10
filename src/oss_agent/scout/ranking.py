"""Deterministic issue scoring for shortlists."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from oss_agent.protocols import IssueCandidate, UntrustedText

_GOOD_LABELS = {
    "good first issue",
    "help wanted",
    "documentation",
    "docs",
    "bug",
    "test",
    "tests",
    "lint",
}


def _labels(raw: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item.lower())
        elif isinstance(item, Mapping) and "name" in item:
            out.append(str(item["name"]).lower())
    return out


def score_issue(issue: Mapping[str, Any]) -> tuple[float, list[str]]:
    """Return (score, reasons). Higher is better."""
    reasons: list[str] = []
    score = 0.0
    labels = _labels(issue.get("labels") or [])
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    assignees = issue.get("assignees") or []
    linked_prs = issue.get("pull_requests") or issue.get("linked_prs") or []
    comments = int(issue.get("comments") or 0)
    body_len = len(body)

    good = [lab for lab in labels if lab in _GOOD_LABELS]
    if good:
        score += 3.0 * len(good)
        reasons.append(f"helpful_labels:{','.join(good)}")

    if not assignees:
        score += 2.0
        reasons.append("unassigned")
    else:
        score -= 3.0
        reasons.append("already_assigned")

    if linked_prs:
        score -= 4.0
        reasons.append("has_linked_pr")
    else:
        score += 1.0
        reasons.append("no_linked_pr")

    if body_len == 0:
        score -= 2.0
        reasons.append("empty_body")
    elif body_len < 4000:
        score += 1.0
        reasons.append("tractable_context")
    else:
        score -= 1.5
        reasons.append("large_context")

    if comments > 20:
        score -= 1.0
        reasons.append("noisy_thread")
    elif comments <= 5:
        score += 0.5
        reasons.append("quiet_thread")

    lower = f"{title}\n{body}".lower()
    if any(k in lower for k in ("rfc", "proposal", "epic", "tracking issue")):
        score -= 3.0
        reasons.append("likely_design_scope")
    if any(k in lower for k in ("typo", "docs", "documentation", "readme")):
        score += 1.5
        reasons.append("docs_or_typo_signal")

    return score, reasons


def rank_issues(
    project_id: str,
    issues: Sequence[Mapping[str, Any]],
    *,
    limit: int = 20,
) -> list[IssueCandidate]:
    ranked: list[IssueCandidate] = []
    for issue in issues:
        score, reasons = score_issue(issue)
        number = int(issue["number"])
        body = str(issue.get("body") or "")
        ranked.append(
            IssueCandidate(
                project_id=project_id,
                number=number,
                title=str(issue.get("title") or ""),
                body=UntrustedText(source=f"issue:{number}", body=body),
                labels=_labels(issue.get("labels") or []),
                score=score,
                score_reasons=reasons,
                url=str(issue.get("html_url") or issue.get("url") or ""),
            )
        )
    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked[:limit]
