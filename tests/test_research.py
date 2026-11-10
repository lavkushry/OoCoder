"""Khoj + last30days-inspired research engine and gateway gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig, ResearchConfig
from oss_agent.db.models import Project
from oss_agent.db.session import create_all, make_engine
from oss_agent.gateway.service import GatewayError, GitHubGatewayService
from oss_agent.orchestrator.engine import Orchestrator
from oss_agent.orchestrator.states import TaskState
from oss_agent.protocols import OutwardActionDraft
from oss_agent.research import research_before_action
from oss_agent.research.corpus import search_corpus

ROOT = Path(__file__).resolve().parents[1]


def test_corpus_finds_contributing() -> None:
    repo = ROOT / "fixtures" / "projects" / "friendly_tiny"
    hits = search_corpus(repo, "AI contribution policy test")
    assert hits
    assert any(h.source in {"contributing", "ai_policy", "local_corpus"} for h in hits)


def test_research_before_action_passes_on_fixture() -> None:
    repo = ROOT / "fixtures" / "projects" / "friendly_tiny"
    issues = ROOT / "fixtures" / "issues" / "friendly_tiny_issues.json"
    brief = research_before_action(
        action_kind="implement",
        query="off-by-one add failing test",
        project_root=repo,
        issues_path=issues,
    )
    assert brief.passed is True
    assert brief.id.startswith("res_")
    assert brief.hits
    assert "Research before action" in brief.summary


def test_research_min_hits_can_block(tmp_path: Path) -> None:
    empty = tmp_path / "empty_proj"
    empty.mkdir()
    brief = research_before_action(
        action_kind="pr",
        query="zzzznotfound",
        project_root=empty,
        config=ResearchConfig(require_min_hits=2, use_issue_signals=False),
    )
    assert brief.passed is False
    assert brief.block_reason


def test_gateway_requires_research_brief(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'r.db'}")
    create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Project(id="p", repo="r", policy_json={}, ai_allowed=True, rate_limits={}))
    session.commit()
    cfg = AppConfig(research=ResearchConfig(require_before_action=True))
    audit = FileAndDbAuditLog(tmp_path / "a.jsonl", session)
    orch = Orchestrator(session, cfg, audit)
    gw = GitHubGatewayService(session, cfg, audit)
    task = orch.create_task(project_id="p", issue_id=None, state=TaskState.CLAIMED)
    with pytest.raises(GatewayError, match="research required"):
        gw.enqueue(OutwardActionDraft(kind="claim", task_id=task.id, payload={"body": "x"}))
    did = gw.enqueue(
        OutwardActionDraft(
            kind="claim",
            task_id=task.id,
            payload={"body": "x", "research_brief_id": "res_abc123def456"},
        )
    )
    assert did.startswith("act_")
    session.close()
