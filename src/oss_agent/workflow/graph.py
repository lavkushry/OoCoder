"""n8n-inspired workflow graph: nodes + connections (not a vendored n8n runtime)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NodeType = Literal["trigger", "app", "if", "wait", "noop"]
# n8n uses output index 0 as "main"; IF nodes expose true/false outputs.
OutputName = Literal["main", "true", "false"]
_OUTPUTS: tuple[OutputName, ...] = ("main", "true", "false")


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    name: str
    type: NodeType
    parameters: dict[str, Any] = field(default_factory=dict)
    # Optional display position (editor-compatible metadata; unused by engine).
    position: tuple[float, float] | None = None


@dataclass(frozen=True)
class Connection:
    source: str
    target: str
    source_output: OutputName = "main"
    target_input: str = "main"


@dataclass(frozen=True)
class WorkflowDefinition:
    """Directed workflow graph, analogous to an n8n workflow JSON document."""

    id: str
    name: str
    nodes: tuple[WorkflowNode, ...]
    connections: tuple[Connection, ...]
    version: int = 1

    def node_map(self) -> dict[str, WorkflowNode]:
        return {n.id: n for n in self.nodes}

    def trigger(self) -> WorkflowNode:
        triggers = [n for n in self.nodes if n.type == "trigger"]
        if len(triggers) != 1:
            raise WorkflowGraphError(f"workflow {self.id} must have exactly one trigger")
        return triggers[0]

    def outgoing(self, node_id: str, output: OutputName = "main") -> list[Connection]:
        return [c for c in self.connections if c.source == node_id and c.source_output == output]

    def validate(self) -> None:
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise WorkflowGraphError("duplicate node ids")
        self.trigger()
        for c in self.connections:
            if c.source not in ids or c.target not in ids:
                raise WorkflowGraphError(f"connection references unknown node: {c}")
        # Every non-trigger node must be reachable from the trigger via any output.
        reach: set[str] = set()
        stack = [self.trigger().id]
        while stack:
            cur = stack.pop()
            if cur in reach:
                continue
            reach.add(cur)
            for out in _OUTPUTS:
                for edge in self.outgoing(cur, out):
                    stack.append(edge.target)
        missing = ids - reach
        if missing:
            raise WorkflowGraphError(f"unreachable nodes: {sorted(missing)}")


class WorkflowGraphError(ValueError):
    pass


def linear_executable_steps(workflow: WorkflowDefinition) -> list[dict[str, Any]]:
    """
    Flatten the main-path app/wait nodes for SuperPlane factory line storage.

    IF false-branches and error sinks are omitted; the engine still evaluates them.
    """
    workflow.validate()
    steps: list[dict[str, Any]] = []
    current: str | None = workflow.trigger().id
    seen: set[str] = set()
    nodes = workflow.node_map()
    while current is not None and current not in seen:
        seen.add(current)
        node = nodes[current]
        if node.type in {"app", "wait"}:
            steps.append(
                {
                    "name": node.name,
                    "app": str(node.parameters.get("app", node.name)),
                    "node_id": node.id,
                    "type": node.type,
                }
            )
        # Prefer main, then true (happy path through IF gates).
        nxt = workflow.outgoing(current, "main") or workflow.outgoing(current, "true")
        current = nxt[0].target if nxt else None
    return steps
