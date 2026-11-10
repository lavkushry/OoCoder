"""GitHub gateway: drafts, policy, rates, disclosure, kill switch, audit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig, PolicyConfig
from oss_agent.db.models import Action, Artifact, RateCounter
from oss_agent.protocols import ActionKind, GatewaySendResult, OutwardActionDraft


class GatewayError(RuntimeError):
    pass


class GitHubGatewayService:
    """Sole path for outward GitHub writes. Transport is injectable for tests."""

    def __init__(
        self,
        session: Session,
        config: AppConfig,
        audit: FileAndDbAuditLog,
        *,
        transport: Any | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.audit = audit
        self.transport = transport  # None => dry-run record only
        # Credentials loaded lazily only inside this class when sending for real.
        self._credentials_loaded = False

    def _policy(self) -> PolicyConfig:
        return self.config.policy

    def enqueue(self, draft: OutwardActionDraft) -> str:
        action_id = f"act_{uuid.uuid4().hex[:16]}"
        row = Action(
            id=action_id,
            task_id=draft.task_id,
            kind=draft.kind,
            draft=dict(draft.payload),
            status="pending",
            evidence_artifact_ids=list(draft.evidence_artifact_ids),
        )
        self.session.add(row)
        self.session.flush()
        self.audit.append(
            actor="gateway",
            event="action.enqueued",
            payload={"action_id": action_id, "kind": draft.kind},
            task_id=draft.task_id,
        )
        # Auto-approve opt-in per kind
        auto = getattr(self._policy().auto_approve, draft.kind)
        if auto:
            self._mark_and_maybe_send(action_id, approved_by="auto")
        return action_id

    async def send_approved(self, draft_id: str, *, approved_by: str) -> GatewaySendResult:
        return self._mark_and_maybe_send(draft_id, approved_by=approved_by)

    def _mark_and_maybe_send(self, draft_id: str, *, approved_by: str) -> GatewaySendResult:
        if self._policy().kill_switch:
            self.audit.append(
                actor="gateway",
                event="action.blocked_kill_switch",
                payload={"action_id": draft_id},
            )
            raise GatewayError("kill switch engaged")

        action = self.session.get(Action, draft_id)
        if action is None:
            raise GatewayError(f"unknown draft {draft_id}")

        kind: ActionKind = action.kind  # type: ignore[assignment]
        if not self._rate_limit_ok(action.task_id, kind):
            raise GatewayError("rate limit exceeded")

        self._assert_evidence_from_sandbox(action)

        body_key = "body" if "body" in action.draft else "message"
        body = str(action.draft.get(body_key, ""))
        disclosure = self._policy().ai_disclosure
        if disclosure not in body:
            body = body.rstrip() + "\n\n---\n" + disclosure + "\n"
            action.draft = {**action.draft, body_key: body}

        action.status = "approved"
        action.approved_by = approved_by
        action.approved_at = datetime.now(UTC)
        self.session.flush()

        remote_id: str | None
        if self.transport is None:
            remote_id = f"dryrun_{action.id}"
        else:
            # Real send would use credentials — only reachable here.
            self._ensure_credentials()
            remote_id = self.transport.send(action.kind, action.draft)

        action.status = "sent"
        action.remote_id = remote_id
        self._bump_rate(action.task_id, kind)
        self.session.flush()
        self.audit.append(
            actor="gateway",
            event="action.sent",
            payload={"action_id": action.id, "remote_id": remote_id, "kind": action.kind},
            task_id=action.task_id,
        )
        return GatewaySendResult(ok=True, remote_id=remote_id, error=None)

    def _ensure_credentials(self) -> None:
        if self._credentials_loaded:
            return
        from oss_agent.gateway.credentials import load_credentials

        _ = load_credentials(self.config.github_credentials_file)
        self._credentials_loaded = True

    def _assert_evidence_from_sandbox(self, action: Action) -> None:
        if action.kind not in {"pr", "push"}:
            return
        ids = list(action.evidence_artifact_ids or [])
        if not ids:
            raise GatewayError("PR/push requires sandbox evidence artifacts")
        for art_id in ids:
            art = self.session.get(Artifact, art_id)
            if art is None or not art.from_sandbox:
                raise GatewayError(f"evidence {art_id} not from sandbox")

    def _project_id_for_task(self, task_id: str) -> str:
        from oss_agent.db.models import Task

        task = self.session.get(Task, task_id)
        if task is None:
            return "unknown"
        return task.project_id

    def _rate_limit_ok(self, task_id: str, kind: ActionKind) -> bool:
        project_id = self._project_id_for_task(task_id)
        limits = self._policy().rate_limits
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        if kind == "comment" or kind == "claim" or kind == "reply":
            count = self._count(project_id, day, "comment")
            return count < limits.max_comments_per_day
        if kind == "pr":
            count = self._count(project_id, day, "pr")
            return count < limits.max_open_prs
        return True

    def _count(self, project_id: str, day: str, kind: str) -> int:
        stmt = select(RateCounter).where(
            RateCounter.project_id == project_id,
            RateCounter.day == day,
            RateCounter.kind == kind,
        )
        row = self.session.execute(stmt).scalar_one_or_none()
        return 0 if row is None else row.count

    def _bump_rate(self, task_id: str, kind: ActionKind) -> None:
        project_id = self._project_id_for_task(task_id)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        bucket = "comment" if kind in {"comment", "claim", "reply"} else kind
        stmt = select(RateCounter).where(
            RateCounter.project_id == project_id,
            RateCounter.day == day,
            RateCounter.kind == bucket,
        )
        row = self.session.execute(stmt).scalar_one_or_none()
        if row is None:
            self.session.add(RateCounter(project_id=project_id, day=day, kind=bucket, count=1))
        else:
            row.count += 1
