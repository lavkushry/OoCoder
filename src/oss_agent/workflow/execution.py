"""Workflow execution state — n8n-like run status + per-node item bags."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from oss_agent.workflow.graph import OutputName

ExecutionStatus = Literal["new", "running", "success", "error", "waiting"]


@dataclass
class WorkflowItem:
    """One data item flowing between nodes (n8n ``item.json`` analogue)."""

    json: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeRunResult:
    success: bool = True
    items: list[WorkflowItem] = field(default_factory=list)
    waiting: bool = False
    error: str | None = None
    # For IF nodes, or to override which output edge to follow.
    branch: OutputName = "main"
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowExecution:
    id: str
    workflow_id: str
    status: ExecutionStatus = "new"
    current_node_id: str | None = None
    run_order: list[str] = field(default_factory=list)
    # node_id -> output items produced by that node
    node_data: dict[str, list[WorkflowItem]] = field(default_factory=dict)
    last_error: str | None = None
    waiting_node_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def last_items(self) -> list[WorkflowItem]:
        if not self.run_order:
            return []
        return list(self.node_data.get(self.run_order[-1], []))
