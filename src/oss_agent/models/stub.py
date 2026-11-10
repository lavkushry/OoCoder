"""Deterministic stub model for offline tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from oss_agent.protocols import ModelMessage, ModelResponse


class StubModelProvider:
    async def complete(
        self,
        *,
        model: Literal["main", "cheap"],
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> ModelResponse:
        _ = (model, tools)
        last = messages[-1].content if messages else ""
        return ModelResponse(
            text=f"stub:{last[:200]}",
            tool_calls=(),
            prompt_tokens=len(last) // 4,
            completion_tokens=10,
        )
