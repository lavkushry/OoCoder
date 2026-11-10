"""Board lanes: Backlog / Running / Review / Done (SuperPlane vocabulary)."""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum
from typing import Any

from oss_agent.factory.states import ExecutionStatus, WorkOrderState


class BoardLane(StrEnum):
    BACKLOG = "Backlog"
    RUNNING = "Running"
    REVIEW = "Review"
    DONE = "Done"


def board_lane(state: str, *, has_active_execution: bool) -> BoardLane:
    """Map persisted work-order state + execution activity to a board lane."""
    if state == WorkOrderState.DRAFT:
        return BoardLane.BACKLOG
    if state == WorkOrderState.CLOSED:
        return BoardLane.DONE
    if state == WorkOrderState.OPEN and has_active_execution:
        return BoardLane.RUNNING
    if state == WorkOrderState.OPEN:
        return BoardLane.REVIEW
    return BoardLane.BACKLOG


def has_active_execution(executions: list[Any]) -> bool:
    active = {ExecutionStatus.PENDING.value, ExecutionStatus.RUNNING.value}
    return any(getattr(e, "status", None) in active for e in executions)


def group_by_lane(orders: list[Any]) -> dict[BoardLane, list[Any]]:
    grouped: dict[BoardLane, list[Any]] = defaultdict(list)
    for order in orders:
        active = has_active_execution(list(getattr(order, "executions", []) or []))
        lane = board_lane(order.state, has_active_execution=active)
        grouped[lane].append(order)
    # Stable column order
    return {lane: grouped.get(lane, []) for lane in BoardLane}
