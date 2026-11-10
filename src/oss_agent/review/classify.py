"""Deterministic first-pass classification of review comments and CI flakes."""

from __future__ import annotations

import re
from typing import Literal

ReviewClass = Literal["nit", "bug", "design", "question", "flake"]

_NIT = re.compile(r"\b(nit|typo|naming|style|nits?)\b", re.I)
_BUG = re.compile(r"\b(bug|broken|regress|incorrect|failing test|crash)\b", re.I)
_DESIGN = re.compile(r"\b(design|architect|approach|alternative|refactor)\b", re.I)
_QUESTION = re.compile(r"\?|\b(why|how come|could we|can you clarify)\b", re.I)
_FLAKE = re.compile(r"\b(flaky|flake|intermittent|timing|race)\b", re.I)


def classify_comment(text: str) -> ReviewClass:
    if _FLAKE.search(text):
        return "flake"
    if _BUG.search(text):
        return "bug"
    if _DESIGN.search(text):
        return "design"
    if _NIT.search(text):
        return "nit"
    if _QUESTION.search(text):
        return "question"
    return "question"


def detect_flake(ci_log: str, known_flakes: list[str]) -> bool:
    if _FLAKE.search(ci_log):
        return True
    lower = ci_log.lower()
    return any(name.lower() in lower for name in known_flakes)


def draft_reply(classification: ReviewClass, comment: str) -> str:
    if classification == "flake":
        return (
            "Thanks — this looks like a known/possible flake. "
            "I'll investigate before retrying CI and avoid blind re-runs."
        )
    if classification == "bug":
        return "Thanks for catching that. I'll prepare a follow-up patch with a regression test."
    if classification == "design":
        return (
            "Appreciate the design feedback. Escalating to the human sponsor "
            "before changing approach."
        )
    if classification == "nit":
        return "Thanks — I'll address the nit in a follow-up commit."
    return "Thanks for the question — drafting a clarifying reply for sponsor review."
