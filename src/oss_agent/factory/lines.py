"""Default factory lines for OSS contribution."""

from __future__ import annotations

from typing import Any

# Paperclip analysis → SuperPlane contribution → Strix-inspired quality gate → PR draft
OSS_CONTRIBUTION_LINE_NAME = "oss-contribution"

OSS_CONTRIBUTION_STEPS: list[dict[str, Any]] = [
    {"name": "analyze", "app": "paperclip_analysis"},
    {"name": "await_analysis_approval", "app": "governance_gate"},
    {"name": "intake", "app": "scout"},
    {"name": "reproduce", "app": "sandbox"},
    {"name": "implement", "app": "patch"},
    {"name": "verify", "app": "verify"},
    {"name": "quality_analysis", "app": "strix_quality"},
    {"name": "await_approval", "app": "gateway_draft"},
]
