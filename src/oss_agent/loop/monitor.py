"""Watch captured checks and reviews until a fixture merge or a stand-down.

The loop does not call GitHub. Repair attempts cap at 2. The next real failure
stands down, records a lesson, and returns. The stand-down comment is a draft;
the gateway injects AI disclosure when a sponsor sends it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from oss_agent.heal.loop import HealOutcome, heal_until_green, repair_budget, stand_down_comment
from oss_agent.learn.models import LessonDraft, LessonRecord
from oss_agent.loop.offline import CiRound
from oss_agent.protocols import SandboxRunner
from oss_agent.review.classify import detect_flake
from oss_agent.review.loop import ReviewHandling, handle_review_thread

_FAILURES = frozenset({"failure", "timed_out", "error"})
_OBSERVATION_CAP = 32

MonitorStatus = Literal["merged", "green", "stand_down", "waiting", "stopped"]


@dataclass(frozen=True)
class MonitorObservation:
    kind: Literal["check", "review", "merged"]
    conclusion: str = ""
    name: str = ""
    log: str = ""
    comment: str = ""
    sha: str = ""


@dataclass(frozen=True)
class MonitorTrack:
    sponsor_approves_merge: bool
    observations: tuple[MonitorObservation, ...]


@dataclass(frozen=True)
class MonitorFixture:
    repo: str
    issue_title: str
    known_flakes: tuple[str, ...]
    max_repairs: int
    accept_design: bool
    merged: MonitorTrack
    stand_down: MonitorTrack


@dataclass(frozen=True)
class MonitorResult:
    status: MonitorStatus
    reason: str
    repair_attempts: int
    observations_consumed: int
    green: bool
    heal: HealOutcome | None
    stand_down_body: str | None
    lessons: tuple[LessonDraft, ...]
    review: ReviewHandling


def checks_as_observations(rounds: Sequence[CiRound]) -> tuple[MonitorObservation, ...]:
    return tuple(
        MonitorObservation(
            kind="check",
            sha=rnd.sha,
            conclusion=rnd.conclusion,
            name=rnd.name,
            log=rnd.log,
        )
        for rnd in rounds
    )


def load_monitor_fixture(path: Path) -> MonitorFixture:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    data = _object(parsed, "fixture")
    tracks = _object(data.get("tracks"), "tracks")
    return MonitorFixture(
        repo=_str(data, "repo"),
        issue_title=_str(data, "issue_title"),
        known_flakes=tuple(_str_list(data.get("known_flakes"), "known_flakes")),
        max_repairs=_int(data, "max_repairs"),
        accept_design=_bool(data, "accept_design"),
        merged=_track(_object(tracks.get("merged"), "merged")),
        stand_down=_track(_object(tracks.get("stand_down"), "stand_down")),
    )


async def monitor_until_merged(
    runner: SandboxRunner,
    *,
    task_id: str,
    workdir: Path,
    repo: str,
    observations: Sequence[MonitorObservation],
    review_comments: Sequence[str] = (),
    known_flakes: Sequence[str] = (),
    max_repairs: int = 2,
    lessons: Sequence[LessonRecord] = (),
    issue_title: str = "",
    sponsor_approves_merge: bool = False,
    accept_design: bool = False,
) -> MonitorResult:
    """Step through captured observations. Stop at merged, or at the repair cap."""
    budget = repair_budget(max_repairs)
    comments = [text for text in review_comments if text]
    comments.extend(obs.comment for obs in observations if obs.kind == "review" and obs.comment)
    first_log = next((obs.log for obs in observations if obs.kind == "check" and obs.log), "")
    review = handle_review_thread(comments, ci_log=first_log, known_flakes=list(known_flakes))
    if review.escalate and not accept_design:
        return _result(
            status="stopped",
            reason="design_escalated",
            review=review,
        )

    repairs = 0
    green = False
    collected: list[LessonDraft] = []
    last_heal: HealOutcome | None = None
    loaded = list(lessons)
    known = list(known_flakes)

    for index, obs in enumerate(observations):
        if index >= _OBSERVATION_CAP:
            return _stand_down_now(
                repo=repo,
                issue_title=issue_title,
                signature="observation_cap",
                attempts=repairs,
                log="observation cap",
                repairs=repairs,
                consumed=index,
                green=green,
                collected=collected,
                last_heal=last_heal,
                review=review,
                reason="observation_cap",
            )
        if obs.kind == "review":
            continue
        if obs.kind == "merged":
            if not green:
                return _result(
                    status="stopped",
                    reason="merge_before_green",
                    repair_attempts=repairs,
                    observations_consumed=index + 1,
                    green=False,
                    heal=last_heal,
                    lessons=tuple(collected),
                    review=review,
                )
            if not sponsor_approves_merge:
                return _result(
                    status="waiting",
                    reason="sponsor_merge_approval",
                    repair_attempts=repairs,
                    observations_consumed=index + 1,
                    green=True,
                    heal=last_heal,
                    lessons=tuple(collected),
                    review=review,
                )
            return _result(
                status="merged",
                reason="merged",
                repair_attempts=repairs,
                observations_consumed=index + 1,
                green=True,
                heal=last_heal,
                lessons=tuple(collected),
                review=review,
            )
        if obs.conclusion in {"pending", "queued", "in_progress"}:
            return _result(
                status="waiting",
                reason="pending",
                repair_attempts=repairs,
                observations_consumed=index + 1,
                green=green,
                heal=last_heal,
                lessons=tuple(collected),
                review=review,
            )
        if obs.conclusion == "success":
            green = True
            continue
        if obs.conclusion not in _FAILURES:
            return _result(
                status="stopped",
                reason="unknown_conclusion",
                repair_attempts=repairs,
                observations_consumed=index + 1,
                green=green,
                heal=last_heal,
                lessons=tuple(collected),
                review=review,
            )
        if detect_flake(obs.log, known):
            return _result(
                status="stopped",
                reason="flake_no_retry",
                repair_attempts=repairs,
                observations_consumed=index + 1,
                green=green,
                heal=last_heal,
                lessons=tuple(collected),
                review=review,
            )
        if repairs >= budget:
            return _stand_down_now(
                repo=repo,
                issue_title=issue_title,
                signature=_signature(obs.log),
                attempts=repairs,
                log=obs.log,
                repairs=repairs,
                consumed=index + 1,
                green=green,
                collected=collected,
                last_heal=last_heal,
                review=review,
                reason="attempt_cap",
            )

        healed = await heal_until_green(
            runner,
            task_id=f"{task_id}-m{index + 1}",
            workdir=workdir,
            repo=repo,
            evidence_log=obs.log,
            known_flakes=known,
            max_repairs=budget - repairs,
            lessons=loaded,
            issue_title=issue_title,
        )
        repairs += healed.repair_attempts
        collected.extend(healed.lessons)
        last_heal = healed
        if healed.reason in {"flake_no_retry", "typo_skipped"}:
            return _result(
                status="stopped",
                reason=healed.reason,
                repair_attempts=repairs,
                observations_consumed=index + 1,
                green=green,
                heal=healed,
                lessons=tuple(collected),
                review=review,
            )
        if not healed.ok:
            signature = healed.lessons[-1].signature if healed.lessons else _signature(obs.log)
            body = stand_down_comment(
                repo=repo,
                signature=signature,
                attempts=repairs,
            )
            return _result(
                status="stand_down",
                reason=healed.reason,
                repair_attempts=repairs,
                observations_consumed=index + 1,
                green=green,
                heal=healed,
                stand_down_body=body,
                lessons=tuple(collected),
                review=review,
            )

    reason = "repaired" if repairs else "ci_green"
    if not green:
        reason = "no_green"
    return _result(
        status="green" if green else "stopped",
        reason=reason if green else "no_green",
        repair_attempts=repairs,
        observations_consumed=len(observations),
        green=green,
        heal=last_heal,
        lessons=tuple(collected),
        review=review,
    )


def _stand_down_now(
    *,
    repo: str,
    issue_title: str,
    signature: str,
    attempts: int,
    log: str,
    repairs: int,
    consumed: int,
    green: bool,
    collected: list[LessonDraft],
    last_heal: HealOutcome | None,
    review: ReviewHandling,
    reason: str,
) -> MonitorResult:
    digest = hashlib.sha256(log.encode()).hexdigest()
    body = stand_down_comment(repo=repo, signature=signature, attempts=attempts)
    collected.append(
        LessonDraft(
            repo=repo,
            signature=signature,
            kind="stand_down",
            rule=(
                f"Stand down on {repo} after {attempts} repair attempts "
                f"for signature {signature}. Do not retry this failure blindly."
            ),
            avoid_files=(),
            failed_pattern="",
            preferred_pattern="",
            module_relpath="",
            title_block=issue_title,
            title_hint="",
            evidence_hash=digest,
            verified=False,
        )
    )
    return _result(
        status="stand_down",
        reason=reason,
        repair_attempts=repairs,
        observations_consumed=consumed,
        green=green,
        heal=last_heal,
        stand_down_body=body,
        lessons=tuple(collected),
        review=review,
    )


def _signature(log: str) -> str:
    return "log:" + hashlib.sha256(log.encode()).hexdigest()[:16]


def _result(
    *,
    status: MonitorStatus,
    reason: str,
    review: ReviewHandling,
    repair_attempts: int = 0,
    observations_consumed: int = 0,
    green: bool = False,
    heal: HealOutcome | None = None,
    stand_down_body: str | None = None,
    lessons: tuple[LessonDraft, ...] = (),
) -> MonitorResult:
    return MonitorResult(
        status=status,
        reason=reason,
        repair_attempts=repair_attempts,
        observations_consumed=observations_consumed,
        green=green,
        heal=heal,
        stand_down_body=stand_down_body,
        lessons=lessons,
        review=review,
    )


def _track(data: Mapping[str, object]) -> MonitorTrack:
    return MonitorTrack(
        sponsor_approves_merge=_bool(data, "sponsor_approves_merge"),
        observations=tuple(_observations(data.get("observations"))),
    )


def _observations(value: object) -> list[MonitorObservation]:
    if not isinstance(value, list):
        raise ValueError("observations")
    found: list[MonitorObservation] = []
    for item in value:
        data = _object(item, "observation")
        kind_raw = _str(data, "kind")
        if kind_raw == "check":
            kind: Literal["check", "review", "merged"] = "check"
        elif kind_raw == "review":
            kind = "review"
        elif kind_raw == "merged":
            kind = "merged"
        else:
            raise ValueError("kind")
        found.append(
            MonitorObservation(
                kind=kind,
                conclusion=str(data.get("conclusion") or ""),
                name=str(data.get("name") or ""),
                log=str(data.get("log") or ""),
                comment=str(data.get("comment") or ""),
                sha=str(data.get("sha") or ""),
            )
        )
    return found


def _object(value: object, label: str) -> dict[str, object]:
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


def _int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
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
