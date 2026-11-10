"""Heal loop: captured failure → repair from evidence → sandbox re-check.

Repair attempts cap at 2. Gateway dry-runs are not attempts. A third real
failure stands down. Flakes are not retried. The stand-down comment is a draft;
the gateway injects AI disclosure when a sponsor sends it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from oss_agent.heal.diagnose import RepairPlan, diagnose_failure
from oss_agent.learn.apply import stand_down_for, verified_for
from oss_agent.learn.models import LessonDraft, LessonRecord, is_one_off_typo
from oss_agent.patching.draft import apply_minimal_fix, unified_diff
from oss_agent.patching.verify import run_local_verification
from oss_agent.protocols import SandboxRunner

REPAIR_ATTEMPT_CAP = 2


@dataclass(frozen=True)
class HealOutcome:
    ok: bool
    reason: str
    repair_attempts: int
    real_failures: int
    last_log: str
    last_hash: str
    diff: str
    files_changed: tuple[str, ...]
    patch_hash: str
    stand_down_body: str | None
    lessons: tuple[LessonDraft, ...]
    from_lesson: bool


def stand_down_comment(*, repo: str, signature: str, attempts: int) -> str:
    """Draft only. The disclosure line is added by the gateway on send."""
    return (
        f"Standing down on {repo} after {attempts} repair attempts. "
        f"Failure signature: {signature}. "
        "The captured checks are still failing. "
        "A human sponsor needs to decide the next step.\n"
    )


def repair_budget(configured: int) -> int:
    """Hard cap is 2. A lower policy limit is honored. Dry-runs are not counted."""
    return min(REPAIR_ATTEMPT_CAP, max(0, configured))


async def verify_or_heal(
    runner: SandboxRunner,
    *,
    task_id: str,
    workdir: Path,
    repo: str,
    known_flakes: Sequence[str] = (),
    max_repairs: int = REPAIR_ATTEMPT_CAP,
    lessons: Sequence[LessonRecord] = (),
    issue_title: str = "",
) -> HealOutcome:
    """Run the sandbox once. Heal only when that captured log is a real failure."""
    first = await run_local_verification(runner, task_id=task_id, workdir=workdir)
    if first.ok:
        return _outcome(
            ok=True,
            reason="green",
            last_log=first.log,
            last_hash=first.content_hash,
        )
    return await heal_until_green(
        runner,
        task_id=f"{task_id}-heal",
        workdir=workdir,
        repo=repo,
        evidence_log=first.log,
        known_flakes=known_flakes,
        max_repairs=max_repairs,
        lessons=lessons,
        issue_title=issue_title,
    )


async def heal_until_green(
    runner: SandboxRunner,
    *,
    task_id: str,
    workdir: Path,
    repo: str,
    evidence_log: str | None = None,
    known_flakes: Sequence[str] = (),
    max_repairs: int = REPAIR_ATTEMPT_CAP,
    lessons: Sequence[LessonRecord] = (),
    issue_title: str = "",
) -> HealOutcome:
    known = list(known_flakes)
    budget = repair_budget(max_repairs)
    loaded = list(lessons)
    log = evidence_log
    if log is None:
        verified = await run_local_verification(runner, task_id=task_id, workdir=workdir)
        if verified.ok:
            return _outcome(
                ok=True,
                reason="green",
                last_log=verified.log,
                last_hash=verified.content_hash,
            )
        log = verified.log

    attempts = 0
    real_failures = 0
    applied: list[_Applied] = []
    seen: set[tuple[str, str, str]] = set()
    originals: dict[str, str] = {}
    from_lesson = False
    last_hash = hashlib.sha256(log.encode()).hexdigest()

    while True:
        if is_one_off_typo(log):
            return _finish(
                ok=False,
                reason="typo_skipped",
                attempts=attempts,
                real_failures=real_failures,
                log=log,
                last_hash=last_hash,
                applied=applied,
                originals=originals,
                workdir=workdir,
                repo=repo,
                issue_title=issue_title,
                from_lesson=from_lesson,
                stand_down=False,
            )
        plan = diagnose_failure(log, workdir, known_flakes=known)
        if plan is not None and plan.flake:
            return _finish(
                ok=False,
                reason="flake_no_retry",
                attempts=attempts,
                real_failures=real_failures,
                log=log,
                last_hash=last_hash,
                applied=applied,
                originals=originals,
                workdir=workdir,
                repo=repo,
                issue_title=issue_title,
                from_lesson=from_lesson,
                stand_down=False,
            )
        if plan is None:
            return _finish(
                ok=False,
                reason="undiagnosed",
                attempts=attempts,
                real_failures=real_failures + 1,
                log=log,
                last_hash=last_hash,
                applied=applied,
                originals=originals,
                workdir=workdir,
                repo=repo,
                issue_title=issue_title,
                from_lesson=from_lesson,
                stand_down=True,
                signature=_signature_or_hash(log),
            )

        learned = verified_for(loaded, plan.signature)
        used_lesson = False
        learned_applies = learned is not None and _contains(
            workdir, learned.module_relpath, learned.failed_pattern
        )
        if learned_applies and learned is not None:
            plan = plan.with_patterns(learned.failed_pattern, learned.preferred_pattern)
            used_lesson = True
        elif stand_down_for(loaded, plan.signature) is not None:
            return _finish(
                ok=False,
                reason="lesson_stand_down",
                attempts=attempts,
                real_failures=real_failures + 1,
                log=log,
                last_hash=last_hash,
                applied=applied,
                originals=originals,
                workdir=workdir,
                repo=repo,
                issue_title=issue_title,
                from_lesson=from_lesson,
                stand_down=True,
                signature=plan.signature,
            )

        real_failures += 1
        if real_failures > budget or attempts >= budget:
            return _finish(
                ok=False,
                reason="attempt_cap",
                attempts=attempts,
                real_failures=real_failures,
                log=log,
                last_hash=last_hash,
                applied=applied,
                originals=originals,
                workdir=workdir,
                repo=repo,
                issue_title=issue_title,
                from_lesson=from_lesson,
                stand_down=True,
                signature=plan.signature,
                avoid=(plan.module_relpath,) if plan.module_relpath else (),
            )

        key = (plan.module_relpath, plan.old, plan.new)
        target = workdir / plan.module_relpath
        if (
            not plan.old
            or not plan.module_relpath
            or not target.is_file()
            or plan.old not in target.read_text(encoding="utf-8")
            or key in seen
        ):
            return _finish(
                ok=False,
                reason="no_progress",
                attempts=attempts,
                real_failures=real_failures,
                log=log,
                last_hash=last_hash,
                applied=applied,
                originals=originals,
                workdir=workdir,
                repo=repo,
                issue_title=issue_title,
                from_lesson=from_lesson,
                stand_down=True,
                signature=plan.signature,
                avoid=(plan.module_relpath,) if plan.module_relpath else (),
            )

        if plan.module_relpath not in originals:
            originals[plan.module_relpath] = target.read_text(encoding="utf-8")
        evidence_hash = hashlib.sha256(log.encode()).hexdigest()
        apply_minimal_fix(
            workdir,
            module_relpath=plan.module_relpath,
            old=plan.old,
            new=plan.new,
        )
        seen.add(key)
        attempts += 1
        if used_lesson:
            from_lesson = True
        prior_signature = plan.signature
        verified = await run_local_verification(
            runner,
            task_id=f"{task_id}-r{attempts}",
            workdir=workdir,
        )
        last_hash = verified.content_hash
        log = verified.log
        fixed = verified.ok or _signature_of(log, workdir, known) != prior_signature
        if not used_lesson:
            applied.append(_Applied(plan=plan, evidence_hash=evidence_hash, verified=fixed))
        if verified.ok:
            return _finish(
                ok=True,
                reason="repaired",
                attempts=attempts,
                real_failures=real_failures,
                log=log,
                last_hash=last_hash,
                applied=applied,
                originals=originals,
                workdir=workdir,
                repo=repo,
                issue_title=issue_title,
                from_lesson=from_lesson,
                stand_down=False,
            )


@dataclass
class _Applied:
    plan: RepairPlan
    evidence_hash: str
    verified: bool


def _finish(
    *,
    ok: bool,
    reason: str,
    attempts: int,
    real_failures: int,
    log: str,
    last_hash: str,
    applied: list[_Applied],
    originals: dict[str, str],
    workdir: Path,
    repo: str,
    issue_title: str,
    from_lesson: bool,
    stand_down: bool,
    signature: str = "",
    avoid: tuple[str, ...] = (),
) -> HealOutcome:
    diff, files, patch_hash = _combined_diff(workdir, originals)
    lessons = _lesson_drafts(
        repo=repo,
        applied=applied,
        stand_down=stand_down,
        signature=signature or (applied[-1].plan.signature if applied else _signature_or_hash(log)),
        issue_title=issue_title,
        avoid=avoid,
        attempts=attempts,
        evidence_hash=last_hash,
    )
    body = None
    if stand_down:
        body = stand_down_comment(repo=repo, signature=signature or "unknown", attempts=attempts)
    return _outcome(
        ok=ok,
        reason=reason,
        repair_attempts=attempts,
        real_failures=real_failures,
        last_log=log,
        last_hash=last_hash,
        diff=diff,
        files_changed=files,
        patch_hash=patch_hash,
        stand_down_body=body,
        lessons=tuple(lessons),
        from_lesson=from_lesson,
    )


def _lesson_drafts(
    *,
    repo: str,
    applied: list[_Applied],
    stand_down: bool,
    signature: str,
    issue_title: str,
    avoid: tuple[str, ...],
    attempts: int,
    evidence_hash: str,
) -> list[LessonDraft]:
    drafts: list[LessonDraft] = []
    for item in applied:
        plan = item.plan
        drafts.append(
            LessonDraft(
                repo=repo,
                signature=plan.signature,
                kind="failure",
                rule=plan.rule,
                avoid_files=plan.avoid_files,
                failed_pattern=plan.old,
                preferred_pattern=plan.new,
                module_relpath=plan.module_relpath,
                title_block="",
                title_hint=plan.title_hint,
                evidence_hash=item.evidence_hash,
                verified=item.verified,
            )
        )
    if stand_down:
        drafts = [draft for draft in drafts if draft.signature != signature or draft.verified]
        avoided = tuple(dict.fromkeys([*avoid, *(item.plan.module_relpath for item in applied)]))
        drafts.append(
            LessonDraft(
                repo=repo,
                signature=signature,
                kind="stand_down",
                rule=(
                    f"Stand down on {repo} after {attempts} repair attempts "
                    f"for signature {signature}. Do not retry this failure blindly."
                ),
                avoid_files=tuple(path for path in avoided if path),
                failed_pattern="",
                preferred_pattern="",
                module_relpath=avoid[0] if len(avoid) == 1 else "",
                title_block=issue_title,
                title_hint="",
                evidence_hash=evidence_hash,
                verified=False,
            )
        )
    return drafts


def _combined_diff(workdir: Path, originals: dict[str, str]) -> tuple[str, tuple[str, ...], str]:
    chunks: list[str] = []
    files: list[str] = []
    for relpath, before in originals.items():
        after = (workdir / relpath).read_text(encoding="utf-8")
        if before == after:
            continue
        chunks.append(unified_diff(relpath, before, after))
        files.append(relpath)
    diff = "".join(chunks)
    digest = hashlib.sha256(diff.encode()).hexdigest() if diff else ""
    return diff, tuple(files), digest


def _contains(workdir: Path, relpath: str, snippet: str) -> bool:
    path = workdir / relpath
    if not relpath or not snippet or not path.is_file():
        return False
    return snippet in path.read_text(encoding="utf-8")


def _signature_of(log: str, workdir: Path, known: list[str]) -> str | None:
    plan = diagnose_failure(log, workdir, known_flakes=known)
    if plan is None:
        return None
    return plan.signature


def _signature_or_hash(log: str) -> str:
    return "log:" + hashlib.sha256(log.encode()).hexdigest()[:16]


def _outcome(
    *,
    ok: bool,
    reason: str,
    repair_attempts: int = 0,
    real_failures: int = 0,
    last_log: str = "",
    last_hash: str = "",
    diff: str = "",
    files_changed: tuple[str, ...] = (),
    patch_hash: str = "",
    stand_down_body: str | None = None,
    lessons: tuple[LessonDraft, ...] = (),
    from_lesson: bool = False,
) -> HealOutcome:
    return HealOutcome(
        ok=ok,
        reason=reason,
        repair_attempts=repair_attempts,
        real_failures=real_failures,
        last_log=last_log,
        last_hash=last_hash,
        diff=diff,
        files_changed=files_changed,
        patch_hash=patch_hash,
        stand_down_body=stand_down_body,
        lessons=lessons,
        from_lesson=from_lesson,
    )
