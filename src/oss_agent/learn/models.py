"""Lesson records. Pure data — persistence lives in the store."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TYPO = re.compile(r"\b(typos?|spelling|nits?)\b", re.I)
_ASSERT = re.compile(r"assert\s+\d+\s*==\s*\d+")
_PHRASE = re.compile(r"still says\s+", re.I)


@dataclass(frozen=True)
class LessonDraft:
    repo: str
    signature: str
    kind: str
    rule: str
    avoid_files: tuple[str, ...]
    failed_pattern: str
    preferred_pattern: str
    module_relpath: str
    title_block: str
    title_hint: str
    evidence_hash: str
    verified: bool


@dataclass(frozen=True)
class LessonRecord:
    id: str
    repo: str
    signature: str
    kind: str
    rule: str
    avoid_files: tuple[str, ...]
    failed_pattern: str
    preferred_pattern: str
    module_relpath: str
    title_block: str
    title_hint: str
    evidence_hash: str
    verified: bool


def is_one_off_typo(text: str, review_class: str | None = None) -> bool:
    """Skip nits and spelling notes. Real failures and maintainer bugs stay."""
    if review_class == "nit":
        return True
    if review_class in {"bug", "design"}:
        return False
    if _ASSERT.search(text) or _PHRASE.search(text):
        return False
    return _TYPO.search(text) is not None
