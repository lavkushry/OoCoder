"""Monitor-until-merged fixture: one heal then merge, and a second failure that stops."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig
from oss_agent.db.models import Action, Project
from oss_agent.db.session import create_all, make_engine
from oss_agent.gateway.service import GitHubGatewayService
from oss_agent.loop.monitor import load_monitor_fixture, monitor_until_merged
from oss_agent.models.coding import (
    CursorLocalRuntime,
    GrokFallbackRuntime,
    host_note,
    select_coding_brain,
)
from oss_agent.orchestrator.engine import Orchestrator
from oss_agent.orchestrator.states import TaskState
from oss_agent.protocols import OutwardActionDraft
from oss_agent.sandbox.fake import FakeSandboxRunner
from oss_agent.sandbox.opensandbox import OpenSandboxRuntime
from oss_agent.sandbox.podman import PodmanSandboxRunner, linux_podman_argv

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "loop" / "monitor_until_merged.json"
REPO = "fixtures/friendly_tiny"


def _tree(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(ROOT / "fixtures" / "projects" / "friendly_tiny", dest)
    module = dest / "src" / "friendly_tiny" / "__init__.py"
    module.write_text(
        module.read_text(encoding="utf-8").replace("return a + b + 1", "return a + b"),
        encoding="utf-8",
    )
    return dest


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 'monitor.db'}")
    create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(
        Project(
            id="proj-friendly",
            repo=REPO,
            policy_json={},
            ai_allowed=True,
            rate_limits={},
        )
    )
    session.commit()
    yield session
    session.close()


@pytest.mark.asyncio
async def test_red_check_heals_once_then_sponsor_merge(tmp_path: Path) -> None:
    fixture = load_monitor_fixture(FIXTURE)
    track = fixture.merged
    workdir = _tree(tmp_path)
    result = await monitor_until_merged(
        FakeSandboxRunner(),
        task_id="merge",
        workdir=workdir,
        repo=fixture.repo,
        observations=track.observations,
        known_flakes=fixture.known_flakes,
        max_repairs=fixture.max_repairs,
        issue_title=fixture.issue_title,
        sponsor_approves_merge=track.sponsor_approves_merge,
        accept_design=fixture.accept_design,
    )
    source = (workdir / "src" / "friendly_tiny" / "__init__.py").read_text(encoding="utf-8")
    assert result.status == "merged"
    assert result.reason == "merged"
    assert result.green is True
    assert result.repair_attempts == 1
    assert result.observations_consumed == len(track.observations)
    assert result.heal is not None and result.heal.ok is True
    assert result.stand_down_body is None
    assert "Intentionally wrong" not in source
    assert "Return the result of add." in source

    blocked = _tree(tmp_path / "hold")
    waiting = await monitor_until_merged(
        FakeSandboxRunner(),
        task_id="hold",
        workdir=blocked,
        repo=fixture.repo,
        observations=track.observations,
        known_flakes=fixture.known_flakes,
        max_repairs=fixture.max_repairs,
        issue_title=fixture.issue_title,
        sponsor_approves_merge=False,
        accept_design=fixture.accept_design,
    )
    assert waiting.status == "waiting"
    assert waiting.reason == "sponsor_merge_approval"
    assert waiting.status != "merged"


@pytest.mark.asyncio
async def test_second_failure_stands_down_and_stops(tmp_path: Path, db: Session) -> None:
    fixture = load_monitor_fixture(FIXTURE)
    track = fixture.stand_down
    result = await monitor_until_merged(
        FakeSandboxRunner(),
        task_id="stand",
        workdir=_tree(tmp_path),
        repo=fixture.repo,
        observations=track.observations,
        known_flakes=fixture.known_flakes,
        max_repairs=fixture.max_repairs,
        issue_title=fixture.issue_title,
        sponsor_approves_merge=track.sponsor_approves_merge,
        accept_design=fixture.accept_design,
    )
    assert result.status == "stand_down"
    assert result.reason == "undiagnosed"
    assert result.repair_attempts == 1
    assert result.observations_consumed == 2
    assert result.observations_consumed < len(track.observations)
    assert result.status != "merged"
    assert any(lesson.kind == "stand_down" for lesson in result.lessons)
    assert result.stand_down_body is not None
    disclosure = AppConfig().policy.ai_disclosure
    assert disclosure not in result.stand_down_body

    cfg = AppConfig()
    cfg.research.require_before_action = False
    audit = FileAndDbAuditLog(tmp_path / "audit.jsonl", db)
    orch = Orchestrator(db, cfg, audit)
    gw = GitHubGatewayService(db, cfg, audit)
    task = orch.create_task(project_id="proj-friendly", issue_id=None, state=TaskState.IN_REVIEW)
    draft_id = gw.enqueue(
        OutwardActionDraft(
            kind="comment",
            task_id=task.id,
            payload={"body": result.stand_down_body, "issue": 3},
        )
    )
    gw._mark_and_maybe_send(draft_id, approved_by="sponsor")
    action = db.get(Action, draft_id)
    assert action is not None
    assert disclosure in str(action.draft.get("body", ""))


def test_cursor_is_coding_brain_only_when_key_present(tmp_path: Path) -> None:
    secret = "super-secret-cursor-key"
    key_file = tmp_path / "cursor_api_key"
    key_file.write_text(secret, encoding="utf-8")
    cursor = select_coding_brain({"CURSOR_API_KEY_FILE": str(key_file), "GROK_API_KEY": "xai-real"})
    assert cursor.brain == "cursor"
    assert cursor.runtime == "local"
    assert cursor.api_called is False
    assert secret not in repr(cursor)

    grok = select_coding_brain({"CURSOR_API_KEY": "…", "GROK_API_KEY": "xai-real"})
    assert grok.brain == "grok"
    assert grok.runtime == "local"
    missing = select_coding_brain({"CURSOR_API_KEY": "", "GROK_API_KEY": ""})
    assert missing.brain == "fixture"
    assert missing.runtime == "none"
    absent_file = select_coding_brain(
        {"CURSOR_API_KEY_FILE": "/etc/oocoder/cursor_api_key"},
        key_file_exists=False,
    )
    assert absent_file.brain == "fixture"


@pytest.mark.asyncio
async def test_default_runtimes_do_not_call_an_api(tmp_path: Path) -> None:
    cursor = await CursorLocalRuntime().implement(cwd=tmp_path, prompt="fix the issue")
    grok = await GrokFallbackRuntime().implement(cwd=tmp_path, prompt="fix the issue")
    assert cursor.api_called is False
    assert cursor.status == "not_executed"
    assert grok.api_called is False
    assert grok.brain == "grok"


def test_linux_podman_command_is_not_run_on_this_host(tmp_path: Path) -> None:
    argv = linux_podman_argv(
        name="oss-agent-fixture",
        image="docker.io/library/python:3.12-slim",
        workdir=tmp_path,
        command=["python", "-m", "pytest", "-q"],
        env={"PATH": "/usr/bin:/bin", "HOME": "/sandbox"},
        network_allowlist=[],
        timeout_sec=30,
        memory_mb=512,
        cpus=1.0,
    )
    rendered = " ".join(argv)
    assert argv[:3] == ["podman", "run", "--rm"]
    assert "--userns=keep-id" in argv
    assert "--network=none" in argv
    assert "--cap-drop=ALL" in argv
    assert "65534" not in rendered
    assert "CURSOR_API_KEY" not in rendered
    note = host_note()
    if sys.platform.startswith("linux"):
        assert "This process is Linux" in note
    else:
        assert sys.platform in note
        assert "was not executed" in note
        runtime = OpenSandboxRuntime(prefer_podman=True)
        assert runtime._podman is None
        assert "Linux" in runtime.host_note
        with pytest.raises(RuntimeError, match="Linux"):
            PodmanSandboxRunner()
