"""Default factory lines for OSS contribution — backed by n8n-style workflow graph."""

from __future__ import annotations

from typing import Any

from oss_agent.workflow.graph import linear_executable_steps
from oss_agent.workflow.oss_contribution import OSS_CONTRIBUTION_WORKFLOW

# Paperclip analysis → SuperPlane contribution → Strix quality → PR draft
# Graph source of truth: OSS_CONTRIBUTION_WORKFLOW (IF + wait nodes).
OSS_CONTRIBUTION_LINE_NAME = OSS_CONTRIBUTION_WORKFLOW.name

OSS_CONTRIBUTION_STEPS: list[dict[str, Any]] = linear_executable_steps(OSS_CONTRIBUTION_WORKFLOW)
