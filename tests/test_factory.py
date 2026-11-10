"""SuperPlane-inspired factory: lifecycle, board lanes, contribution line."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from oss_agent.db.session import create_all, make_engine
from oss_agent.factory.board import BoardLane, board_lane, group_by_lane
from oss_agent.factory.lines import OSS_CONTRIBUTION_LINE_NAME, OSS_CONTRIBUTION_STEPS
from oss_agent.factory.runner import run_oss_factory_demo
from oss_agent.factory.service import FactoryError, FactoryService
from oss_agent.factory.states import CloseResult, WorkOrderState


@pytest.fixture
def session(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'f.db'}")
    create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def test_work_order_lifecycle_and_board(session) -> None:
    svc = FactoryService(session)
    factory = svc.create_factory(key="demo", name="Demo Factory")
    assert any(line.name == OSS_CONTRIBUTION_LINE_NAME for line in factory.lines)
    assert factory.lines[0].steps == OSS_CONTRIBUTION_STEPS

    order = svc.create_work_order(factory_id=factory.id, title="Fix typo")
    assert order.state == WorkOrderState.DRAFT.value
    assert board_lane(order.state, has_active_execution=False) == BoardLane.BACKLOG

    execution = svc.dispatch(order.id, line_name=OSS_CONTRIBUTION_LINE_NAME)
    assert order.state == WorkOrderState.OPEN.value
    assert board_lane(order.state, has_active_execution=True) == BoardLane.RUNNING
    assert execution.step_name == "project_research"

    nxt = svc.advance_or_fail(execution.id, passed=True)
    assert nxt is not None
    assert nxt.step_name == "analyze"

    # Finish remaining steps quickly as pass-through for lifecycle test
    while nxt is not None:
        nxt = svc.advance_or_fail(nxt.id, passed=True)

    session.refresh(order)
    _ = list(order.executions)
    assert board_lane(order.state, has_active_execution=False) == BoardLane.REVIEW

    svc.update_status(order.id, state=WorkOrderState.CLOSED, result=CloseResult.COMPLETED)
    assert board_lane(order.state, has_active_execution=False) == BoardLane.DONE

    board = group_by_lane(svc.list_orders(factory.id))
    assert any(o.id == order.id for o in board[BoardLane.DONE])


def test_draft_close_only_rejected(session) -> None:
    svc = FactoryService(session)
    factory = svc.create_factory(key="x", name="X")
    order = svc.create_work_order(factory_id=factory.id, title="abandon me")
    with pytest.raises(FactoryError):
        svc.update_status(order.id, state=WorkOrderState.CLOSED, result=CloseResult.COMPLETED)
    svc.update_status(order.id, state=WorkOrderState.CLOSED, result=CloseResult.REJECTED)
    assert order.result == "rejected"


@pytest.mark.asyncio
async def test_factory_line_runs_fixture(session, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = await run_oss_factory_demo(root, tmp_path, session)
    assert result.steps_passed == [
        "project_research",
        "analyze",
        "await_analysis_approval",
        "intake",
        "discuss",
        "await_discuss_approval",
        "reproduce",
        "implement",
        "verify",
        "quality_analysis",
        "await_approval",
        "review_iterate",
        "await_merge",
    ]
    assert result.analysis_recommendation == "go"
    assert result.chosen_issue == 3
    assert result.discussion_action_id is not None
    assert result.pr_draft_id is not None
    assert result.merged is True
    assert result.task_state == "MERGED"
    assert result.final_state == "closed"
    assert result.result == "completed"
