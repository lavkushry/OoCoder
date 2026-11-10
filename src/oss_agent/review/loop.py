"""Handle a simulated review thread: classify, flake-detect, draft replies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oss_agent.review.classify import classify_comment, detect_flake, draft_reply


@dataclass(frozen=True)
class ReviewHandling:
    items: list[dict[str, Any]]
    escalate: bool
    flake_detected: bool


def handle_review_thread(
    comments: list[str],
    *,
    ci_log: str = "",
    known_flakes: list[str] | None = None,
) -> ReviewHandling:
    known = known_flakes or []
    flake = detect_flake(ci_log, known) if ci_log else False
    items: list[dict[str, Any]] = []
    escalate = False
    for text in comments:
        cls = classify_comment(text)
        if cls == "flake":
            flake = True
        if cls == "design":
            escalate = True
        items.append(
            {
                "source_comment": text,
                "class": cls,
                "draft_reply": draft_reply(cls, text),
                "status": "pending_approval",
            }
        )
    if flake and ci_log:
        items.append(
            {
                "source_comment": ci_log[:500],
                "class": "flake",
                "draft_reply": draft_reply("flake", ci_log),
                "status": "pending_approval",
            }
        )
    return ReviewHandling(items=items, escalate=escalate, flake_detected=flake)
