"""Apply stored lessons before choose, implement, and repair."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from oss_agent.learn.models import LessonRecord
from oss_agent.patching.draft import PatchResult, apply_minimal_fix


def lesson_rules(lessons: Sequence[LessonRecord]) -> list[str]:
    return [lesson.rule for lesson in lessons if lesson.rule]


def title_from_lessons(default: str, lessons: Sequence[LessonRecord]) -> str:
    for lesson in lessons:
        hint = lesson.title_hint.strip()
        if hint:
            return hint
    return default


def verified_for(lessons: Sequence[LessonRecord], signature: str) -> LessonRecord | None:
    for lesson in lessons:
        if (
            lesson.verified
            and lesson.signature == signature
            and lesson.failed_pattern
            and lesson.preferred_pattern
        ):
            return lesson
    return None


def stand_down_for(lessons: Sequence[LessonRecord], signature: str) -> LessonRecord | None:
    for lesson in lessons:
        if lesson.kind == "stand_down" and not lesson.verified and lesson.signature == signature:
            return lesson
    return None


def skipped_by_lesson(title: str, body: str, lessons: Sequence[LessonRecord]) -> bool:
    """Stand-down lessons block the same title or a named file. Verified repairs do not."""
    blob = f"{title}\n{body}".casefold()
    for lesson in lessons:
        if lesson.verified or lesson.preferred_pattern:
            continue
        if lesson.kind != "stand_down":
            continue
        if lesson.title_block and title.casefold() == lesson.title_block.casefold():
            return True
        for path in lesson.avoid_files:
            if path and path.casefold() in blob:
                return True
    return False


def implementation_blocked(module_relpath: str, lessons: Sequence[LessonRecord]) -> bool:
    """Stand-down and maintainer lessons can name files to leave alone.

    A sandbox-verified repair for that same file is the lesson to follow, so it
    does not block.
    """
    verified_here = any(
        lesson.verified and lesson.module_relpath == module_relpath and lesson.preferred_pattern
        for lesson in lessons
    )
    if verified_here:
        return False
    return any(
        lesson.kind in {"stand_down", "maintainer_correction"}
        and not lesson.verified
        and not lesson.preferred_pattern
        and module_relpath in lesson.avoid_files
        for lesson in lessons
    )


def apply_verified_lessons(workdir: Path, lessons: Sequence[LessonRecord]) -> PatchResult | None:
    """Apply sandbox-verified patterns whose failed text is still in the tree."""
    last: PatchResult | None = None
    for lesson in lessons:
        if not lesson.verified or not lesson.failed_pattern or not lesson.preferred_pattern:
            continue
        if not lesson.module_relpath:
            continue
        path = workdir / lesson.module_relpath
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if lesson.failed_pattern not in text:
            continue
        last = apply_minimal_fix(
            workdir,
            module_relpath=lesson.module_relpath,
            old=lesson.failed_pattern,
            new=lesson.preferred_pattern,
        )
    return last


def apply_implementation(
    workdir: Path,
    *,
    module_relpath: str,
    old: str,
    new: str,
    lessons: Sequence[LessonRecord],
) -> tuple[PatchResult | None, str]:
    """Lesson first, then the fixture edit if its old text is still present."""
    if implementation_blocked(module_relpath, lessons):
        return None, "blocked"
    lesson_patch = apply_verified_lessons(workdir, lessons)
    target = workdir / module_relpath
    if target.is_file() and old in target.read_text(encoding="utf-8"):
        patch = apply_minimal_fix(workdir, module_relpath=module_relpath, old=old, new=new)
        return patch, "applied_fixture"
    if lesson_patch is not None:
        return lesson_patch, "applied_lesson"
    return None, "missing"
