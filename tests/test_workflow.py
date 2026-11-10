"""n8n-inspired workflow engine tests."""

from __future__ import annotations

import pytest

from oss_agent.factory.lines import OSS_CONTRIBUTION_STEPS
from oss_agent.workflow import (
    OSS_CONTRIBUTION_WORKFLOW,
    Connection,
    NodeRunResult,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowGraphError,
    WorkflowItem,
    WorkflowNode,
    linear_executable_steps,
)


def test_oss_contribution_workflow_valid() -> None:
    OSS_CONTRIBUTION_WORKFLOW.validate()
    steps = linear_executable_steps(OSS_CONTRIBUTION_WORKFLOW)
    assert [s["name"] for s in steps] == [
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
    assert steps == OSS_CONTRIBUTION_STEPS


def test_engine_happy_path_with_if_and_wait() -> None:
    engine = WorkflowEngine(OSS_CONTRIBUTION_WORKFLOW)
    ex = engine.start({"issue": 3})
    node = engine.current_node(ex)
    assert node is not None and node.name == "project_research"

    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": True})]),
    )
    node = engine.current_node(ex)
    assert node is not None and node.name == "analyze"

    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": True})]),
    )
    node = engine.current_node(ex)
    assert node is not None and node.name == "await_analysis_approval"

    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": True, "approved": True})]),
    )
    assert engine.current_node(ex) is not None
    assert engine.current_node(ex).name == "intake"  # type: ignore[union-attr]

    for name in (
        "intake",
        "discuss",
        "await_discuss_approval",
        "reproduce",
        "implement",
        "verify",
        "quality_analysis",
    ):
        assert engine.current_node(ex) is not None
        assert engine.current_node(ex).name == name  # type: ignore[union-attr]
        engine.complete(
            ex,
            NodeRunResult(success=True, items=[WorkflowItem(json={"passed": True})]),
        )

    assert engine.current_node(ex) is not None
    assert engine.current_node(ex).name == "await_approval"  # type: ignore[union-attr]
    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": True})]),
    )
    assert engine.current_node(ex) is not None
    assert engine.current_node(ex).name == "review_iterate"  # type: ignore[union-attr]
    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": True})]),
    )
    assert engine.current_node(ex) is not None
    assert engine.current_node(ex).name == "await_merge"  # type: ignore[union-attr]
    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": True})]),
    )
    assert ex.status == "success"
    assert engine.current_node(ex) is None


def test_engine_blocks_when_analysis_not_go() -> None:
    engine = WorkflowEngine(OSS_CONTRIBUTION_WORKFLOW)
    ex = engine.start({})
    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": True})]),
    )
    assert engine.current_node(ex) is not None
    assert engine.current_node(ex).name == "analyze"  # type: ignore[union-attr]
    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": False})]),
    )
    assert ex.status == "error"
    assert ex.last_error == "workflow_blocked"


def test_engine_blocks_when_research_fails() -> None:
    engine = WorkflowEngine(OSS_CONTRIBUTION_WORKFLOW)
    ex = engine.start({})
    engine.complete(
        ex,
        NodeRunResult(success=True, items=[WorkflowItem(json={"passed": False})]),
    )
    assert ex.status == "error"
    assert ex.last_error == "workflow_blocked"


def test_engine_waiting_resume() -> None:
    wf = WorkflowDefinition(
        id="wf_wait",
        name="wait-demo",
        nodes=(
            WorkflowNode(id="t", name="t", type="trigger"),
            WorkflowNode(id="w", name="gate", type="wait", parameters={"app": "gate"}),
            WorkflowNode(id="d", name="done", type="noop"),
        ),
        connections=(Connection("t", "w"), Connection("w", "d")),
    )
    engine = WorkflowEngine(wf)
    ex = engine.start({})
    assert engine.current_node(ex) is not None
    engine.complete(ex, NodeRunResult(waiting=True, items=[WorkflowItem()]))
    assert ex.status == "waiting"
    engine.resume_waiting(ex, approved=True)
    assert ex.status == "success"


def test_unreachable_node_rejected() -> None:
    wf = WorkflowDefinition(
        id="bad",
        name="bad",
        nodes=(
            WorkflowNode(id="t", name="t", type="trigger"),
            WorkflowNode(id="orphan", name="orphan", type="app"),
        ),
        connections=(),
    )
    with pytest.raises(WorkflowGraphError, match="unreachable"):
        wf.validate()
