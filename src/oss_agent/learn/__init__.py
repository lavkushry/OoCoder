"""Self-learning: durable lessons that change the next contribution run."""

from oss_agent.learn.apply import (
    apply_implementation,
    apply_verified_lessons,
    implementation_blocked,
    lesson_rules,
    title_from_lessons,
)
from oss_agent.learn.models import LessonDraft, LessonRecord, is_one_off_typo
from oss_agent.learn.store import LessonStore, audit_lessons

__all__ = [
    "LessonDraft",
    "LessonRecord",
    "LessonStore",
    "apply_implementation",
    "apply_verified_lessons",
    "audit_lessons",
    "implementation_blocked",
    "is_one_off_typo",
    "lesson_rules",
    "title_from_lessons",
]
