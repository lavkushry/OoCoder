"""n8n-inspired workflow execution engine (graph walk + item passthrough)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from oss_agent.workflow.execution import (
    NodeRunResult,
    WorkflowExecution,
    WorkflowItem,
)
from oss_agent.workflow.graph import (
    OutputName,
    WorkflowDefinition,
    WorkflowGraphError,
    WorkflowNode,
)

NodeHandler = Callable[[WorkflowNode, list[WorkflowItem], WorkflowExecution], NodeRunResult]


class WorkflowEngine:
    """
    Pull-based executor:

    1. ``start`` activates the trigger and advances automatic nodes (if/noop).
    2. Caller runs the current ``app``/``wait`` node, then ``complete``.
    3. Engine follows connections; IF nodes read ``passed`` from last items.
    """

    def __init__(self, workflow: WorkflowDefinition) -> None:
        workflow.validate()
        self.workflow = workflow
        self._nodes = workflow.node_map()

    def start(self, trigger_data: dict[str, Any] | None = None) -> WorkflowExecution:
        execution = WorkflowExecution(
            id=f"wfe_{uuid.uuid4().hex[:12]}",
            workflow_id=self.workflow.id,
            status="running",
            current_node_id=self.workflow.trigger().id,
        )
        trigger = self.workflow.trigger()
        result = NodeRunResult(
            success=True,
            items=[WorkflowItem(json=dict(trigger_data or {}))],
            detail={"node_type": "trigger"},
        )
        self._apply_result(execution, trigger, result)
        self._advance_automatic(execution)
        return execution

    def current_node(self, execution: WorkflowExecution) -> WorkflowNode | None:
        if execution.status not in {"running", "waiting"}:
            return None
        if execution.current_node_id is None:
            return None
        return self._nodes[execution.current_node_id]

    def complete(
        self,
        execution: WorkflowExecution,
        result: NodeRunResult,
    ) -> WorkflowExecution:
        node = self.current_node(execution)
        if node is None:
            raise WorkflowGraphError("no current node to complete")
        if execution.status == "waiting" and result.waiting:
            return execution
        self._apply_result(execution, node, result)
        if execution.status in {"error", "waiting", "success"}:
            return execution
        self._advance_automatic(execution)
        return execution

    def resume_waiting(
        self,
        execution: WorkflowExecution,
        *,
        approved: bool,
        detail: dict[str, Any] | None = None,
    ) -> WorkflowExecution:
        """Resume an n8n-style wait node (human / webhook gate)."""
        if execution.status != "waiting" or execution.waiting_node_id is None:
            raise WorkflowGraphError("execution is not waiting")
        execution.status = "running"
        execution.current_node_id = execution.waiting_node_id
        execution.waiting_node_id = None
        items = execution.last_items() or [WorkflowItem()]
        for item in items:
            item.json["approved"] = approved
        return self.complete(
            execution,
            NodeRunResult(
                success=approved,
                items=items,
                waiting=False,
                detail=detail or {"approved": approved},
                branch="main",
                error=None if approved else "wait_rejected",
            ),
        )

    def _apply_result(
        self,
        execution: WorkflowExecution,
        node: WorkflowNode,
        result: NodeRunResult,
    ) -> None:
        execution.run_order.append(node.id)
        execution.node_data[node.id] = list(result.items)
        execution.meta[f"detail:{node.id}"] = result.detail
        if result.waiting:
            execution.status = "waiting"
            execution.waiting_node_id = node.id
            execution.current_node_id = node.id
            return
        if not result.success:
            execution.status = "error"
            execution.last_error = result.error or f"node {node.name} failed"
            execution.current_node_id = None
            return
        out: OutputName = result.branch
        if node.type == "if":
            out = result.branch if result.branch in {"true", "false"} else "false"
        edges = self.workflow.outgoing(node.id, out)
        if not edges:
            # Terminal success when happy-path has no further edges.
            execution.status = "success"
            execution.current_node_id = None
            return
        execution.current_node_id = edges[0].target
        execution.status = "running"

    def _advance_automatic(self, execution: WorkflowExecution) -> None:
        """Auto-run trigger leftovers, IF, and noop nodes."""
        guard = 0
        while execution.status == "running" and execution.current_node_id:
            guard += 1
            if guard > len(self._nodes) * 3:
                raise WorkflowGraphError("automatic advance loop detected")
            node = self._nodes[execution.current_node_id]
            if node.type in {"app", "wait"}:
                return
            if node.type == "noop":
                items = execution.last_items() or [WorkflowItem()]
                if node.parameters.get("terminal") == "blocked":
                    self._apply_result(
                        execution,
                        node,
                        NodeRunResult(
                            success=False,
                            items=items,
                            error="workflow_blocked",
                            detail={"terminal": "blocked"},
                        ),
                    )
                    continue
                self._apply_result(
                    execution,
                    node,
                    NodeRunResult(success=True, items=items, detail={"noop": True}),
                )
                continue
            if node.type == "if":
                self._apply_result(execution, node, self._eval_if(node, execution))
                continue
            if node.type == "trigger":
                # Already applied in start(); should not re-enter.
                return
            raise WorkflowGraphError(f"unknown node type {node.type}")

    def _eval_if(self, node: WorkflowNode, execution: WorkflowExecution) -> NodeRunResult:
        items = execution.last_items() or [WorkflowItem()]
        key = str(node.parameters.get("boolean_key", "passed"))
        expected = node.parameters.get("equals", True)
        value = items[0].json.get(key, False)
        ok = value == expected
        return NodeRunResult(
            success=True,
            items=items,
            branch="true" if ok else "false",
            detail={"if": key, "value": value, "branch": "true" if ok else "false"},
        )
