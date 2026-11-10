"""SQLite lesson store keyed by repo + failure signature."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from oss_agent.db.models import Lesson
from oss_agent.learn.models import LessonDraft, LessonRecord, is_one_off_typo
from oss_agent.protocols import AuditLog

_PATH = re.compile(r"\b[\w./-]+\.py\b")


class LessonStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, draft: LessonDraft) -> LessonRecord:
        stmt = select(Lesson).where(
            Lesson.repo == draft.repo,
            Lesson.signature == draft.signature,
        )
        row = self.session.execute(stmt).scalar_one_or_none()
        if row is None:
            row = Lesson(
                id=f"les_{uuid.uuid4().hex[:12]}",
                repo=draft.repo,
                signature=draft.signature,
                kind=draft.kind,
                rule=draft.rule,
                avoid_files=list(draft.avoid_files),
                failed_pattern=draft.failed_pattern,
                preferred_pattern=draft.preferred_pattern,
                module_relpath=draft.module_relpath,
                title_block=draft.title_block,
                title_hint=draft.title_hint,
                evidence_hash=draft.evidence_hash,
                verified=draft.verified,
            )
            self.session.add(row)
        else:
            _merge(row, draft)
        self.session.flush()
        return _record(row)

    def upsert_many(self, drafts: Sequence[LessonDraft]) -> list[LessonRecord]:
        return [self.upsert(draft) for draft in drafts]

    def for_repo(self, repo: str) -> list[LessonRecord]:
        stmt = select(Lesson).where(Lesson.repo == repo).order_by(Lesson.created_at, Lesson.id)
        return [_record(row) for row in self.session.execute(stmt).scalars()]

    def record_maintainer(self, repo: str, comment: str, review_class: str) -> LessonRecord | None:
        """Persist a maintainer correction. One-off typos are dropped."""
        draft = maintainer_draft(repo, comment, review_class)
        if draft is None:
            return None
        return self.upsert(draft)


def maintainer_draft(repo: str, comment: str, review_class: str) -> LessonDraft | None:
    if is_one_off_typo(comment, review_class):
        return None
    if review_class not in {"bug", "design"}:
        return None
    paths = tuple(dict.fromkeys(_PATH.findall(comment)))
    digest = hashlib.sha256(comment.encode()).hexdigest()[:16]
    return LessonDraft(
        repo=repo,
        signature=f"maintainer:{digest}",
        kind="maintainer_correction",
        rule=comment.strip()[:500],
        avoid_files=paths,
        failed_pattern="",
        preferred_pattern="",
        module_relpath=paths[0] if len(paths) == 1 else "",
        title_block="",
        title_hint="",
        evidence_hash="",
        verified=False,
    )


def audit_lessons(audit: AuditLog, drafts: Sequence[LessonDraft], *, task_id: str) -> None:
    for draft in drafts:
        audit.append(
            actor="learn",
            event="lesson.recorded",
            payload={
                "repo": draft.repo,
                "signature": draft.signature,
                "kind": draft.kind,
                "verified": draft.verified,
                "module": draft.module_relpath,
                "rule": draft.rule[:240],
            },
            task_id=task_id,
        )


def _merge(row: Lesson, draft: LessonDraft) -> None:
    if draft.verified:
        row.verified = True
        if draft.preferred_pattern:
            row.preferred_pattern = draft.preferred_pattern
            row.failed_pattern = draft.failed_pattern
            if draft.module_relpath:
                row.module_relpath = draft.module_relpath
    if draft.rule:
        row.rule = draft.rule
    if draft.avoid_files:
        merged = list(dict.fromkeys([*_strings(row.avoid_files), *draft.avoid_files]))
        row.avoid_files = merged
    if draft.title_block and not row.title_block:
        row.title_block = draft.title_block
    if draft.title_hint and not row.title_hint:
        row.title_hint = draft.title_hint
    if draft.evidence_hash:
        row.evidence_hash = draft.evidence_hash
    if draft.kind == "stand_down" and not row.verified:
        row.kind = "stand_down"


def _record(row: Lesson) -> LessonRecord:
    return LessonRecord(
        id=row.id,
        repo=row.repo,
        signature=row.signature,
        kind=row.kind,
        rule=row.rule or "",
        avoid_files=_strings(row.avoid_files),
        failed_pattern=row.failed_pattern or "",
        preferred_pattern=row.preferred_pattern or "",
        module_relpath=row.module_relpath or "",
        title_block=row.title_block or "",
        title_hint=row.title_hint or "",
        evidence_hash=row.evidence_hash or "",
        verified=bool(row.verified),
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
