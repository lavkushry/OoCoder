"""n8n-inspired workflow engine for oss-agent factory lines."""

from oss_agent.workflow.engine import WorkflowEngine
from oss_agent.workflow.execution import NodeRunResult, WorkflowExecution, WorkflowItem
from oss_agent.workflow.graph import (
    Connection,
    WorkflowDefinition,
    WorkflowGraphError,
    WorkflowNode,
    linear_executable_steps,
)
from oss_agent.workflow.oss_contribution import OSS_CONTRIBUTION_WORKFLOW

__all__ = [
    "OSS_CONTRIBUTION_WORKFLOW",
    "Connection",
    "NodeRunResult",
    "WorkflowDefinition",
    "WorkflowEngine",
    "WorkflowExecution",
    "WorkflowGraphError",
    "WorkflowItem",
    "WorkflowNode",
    "linear_executable_steps",
]
