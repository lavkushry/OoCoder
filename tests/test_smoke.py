"""Bootstrap smoke tests."""

from __future__ import annotations

from oss_agent import __version__
from oss_agent.config import AppConfig
from oss_agent.orchestrator.states import TaskState, TransitionTable


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_default_config_requires_approvals() -> None:
    cfg = AppConfig()
    assert cfg.policy.auto_approve.pr is False
    assert cfg.policy.auto_approve.claim is False
    assert cfg.host == "127.0.0.1"


def test_forward_transition_legal() -> None:
    TransitionTable.assert_transition(TaskState.SCOUTING, TaskState.SHORTLISTED)


def test_illegal_transition_raises() -> None:
    try:
        TransitionTable.assert_transition(TaskState.SCOUTING, TaskState.MERGED)
    except ValueError as exc:
        assert "illegal transition" in str(exc)
    else:
        raise AssertionError("expected ValueError")
