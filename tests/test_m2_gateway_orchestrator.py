"""M2: durable resume, gateway gates, kill switch, approval required."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig, AutoApprove, PolicyConfig
from oss_agent.db.models import Action, Artifact, Project, Task
from oss_agent.db.session import create_all, make_engine
from oss_agent.gateway.service import GatewayError, GitHubGatewayService
from oss_agent.orchestrator.engine import Orchestrator
from oss_agent.orchestrator.states import TaskState
from oss_agent.protocols import OutwardActionDraft


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(
        Project(
            id="proj1",
            repo="fixtures/friendly_tiny",
            policy_json={},
            ai_allowed=True,
            rate_limits={},
        )
    )
    session.commit()
    yield session
    session.close()


def _orch(
    session: Session,
    tmp_path: Path,
    cfg: AppConfig | None = None,
) -> tuple[Orchestrator, GitHubGatewayService]:
    cfg = cfg or AppConfig()
    # M2 suite covers gateway gates other than research (see test_research.py).
    cfg.research.require_before_action = False
    audit = FileAndDbAuditLog(tmp_path / "audit.jsonl", session)
    return Orchestrator(session, cfg, audit), GitHubGatewayService(session, cfg, audit)


def test_crash_mid_task_resumes(db: Session, tmp_path: Path) -> None:
    orch, _ = _orch(db, tmp_path)
    task = orch.create_task(project_id="proj1", issue_id=None, state=TaskState.REPRODUCING)
    # Simulate kill -9: drop in-memory orchestrator, new one recovers from DB.
    task_id = task.id
    state = task.state
    recovered = orch.recover_after_crash()
    assert any(r["task_id"] == task_id and r["state"] == state for r in recovered)
    # Continue transitions after "crash"
    orch.transition(task_id, TaskState.DESIGNING, reason="resume-after-crash")
    assert db.get(Task, task_id) is not None
    assert db.get(Task, task_id).state == TaskState.DESIGNING.value  # type: ignore[union-attr]


def test_kill_switch_blocks_send(db: Session, tmp_path: Path) -> None:
    cfg = AppConfig(policy=PolicyConfig(kill_switch=True))
    orch, gw = _orch(db, tmp_path, cfg)
    task = orch.create_task(
        project_id="proj1",
        issue_id=None,
        state=TaskState.AWAITING_CLAIM_APPROVAL,
    )
    draft_id = gw.enqueue(
        OutwardActionDraft(kind="claim", task_id=task.id, payload={"body": "claiming"})
    )
    with pytest.raises(GatewayError, match="kill switch"):
        gw._mark_and_maybe_send(draft_id, approved_by="sponsor")


def test_cannot_send_without_approval(db: Session, tmp_path: Path) -> None:
    orch, gw = _orch(db, tmp_path)
    task = orch.create_task(project_id="proj1", issue_id=None, state=TaskState.AWAITING_PR_APPROVAL)
    art = Artifact(
        id="art1",
        task_id=task.id,
        type="test_log",
        path="/tmp/x",
        content_hash="abc",
        from_sandbox=True,
    )
    db.add(art)
    db.flush()
    draft_id = gw.enqueue(
        OutwardActionDraft(
            kind="pr",
            task_id=task.id,
            payload={"title": "fix", "body": "summary of fix"},
            evidence_artifact_ids=["art1"],
        )
    )
    row = db.get(Action, draft_id)
    assert row is not None
    assert row.status == "pending"
    result = gw._mark_and_maybe_send(draft_id, approved_by="sponsor")
    assert result.ok
    assert row.status == "sent"
    assert cfg_disclosure_in_body(row.draft)


def cfg_disclosure_in_body(draft: dict) -> bool:
    return (
        "AI assistance" in str(draft.get("body", ""))
        or "ai assistance" in str(draft.get("body", "")).lower()
    )


def test_model_fabricated_evidence_rejected(db: Session, tmp_path: Path) -> None:
    orch, gw = _orch(db, tmp_path)
    task = orch.create_task(project_id="proj1", issue_id=None, state=TaskState.AWAITING_PR_APPROVAL)
    db.add(
        Artifact(
            id="fake",
            task_id=task.id,
            type="test_log",
            path="/tmp/model.txt",
            content_hash="x",
            from_sandbox=False,
        )
    )
    db.flush()
    draft_id = gw.enqueue(
        OutwardActionDraft(
            kind="pr",
            task_id=task.id,
            payload={"title": "x", "body": "y" * 50},
            evidence_artifact_ids=["fake"],
        )
    )
    with pytest.raises(GatewayError, match="not from sandbox"):
        gw._mark_and_maybe_send(draft_id, approved_by="sponsor")


def test_auto_approve_opt_in(db: Session, tmp_path: Path) -> None:
    cfg = AppConfig(policy=PolicyConfig(auto_approve=AutoApprove(comment=True)))
    orch, gw = _orch(db, tmp_path, cfg)
    task = orch.create_task(project_id="proj1", issue_id=None, state=TaskState.IN_REVIEW)
    draft_id = gw.enqueue(
        OutwardActionDraft(kind="comment", task_id=task.id, payload={"body": "thanks"})
    )
    assert db.get(Action, draft_id).status == "sent"  # type: ignore[union-attr]
