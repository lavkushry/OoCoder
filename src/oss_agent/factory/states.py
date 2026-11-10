"""Work-order lifecycle mirrored from SuperPlane factory design."""

from __future__ import annotations

from enum import StrEnum


class WorkOrderState(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"


class CloseResult(StrEnum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"


class ExecutionResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    CANCEL = "cancel"


# open -> closed accepts completed|rejected|failed
# draft -> closed accepts rejected only
CLOSE_RESULTS: dict[WorkOrderState, frozenset[CloseResult]] = {
    WorkOrderState.OPEN: frozenset(
        {CloseResult.COMPLETED, CloseResult.REJECTED, CloseResult.FAILED}
    ),
    WorkOrderState.DRAFT: frozenset({CloseResult.REJECTED}),
    WorkOrderState.CLOSED: frozenset(),
}


def can_transition(src: WorkOrderState, dst: WorkOrderState) -> bool:
    if src == dst:
        return False
    allowed = {
        WorkOrderState.DRAFT: {WorkOrderState.OPEN, WorkOrderState.CLOSED},
        WorkOrderState.OPEN: {WorkOrderState.DRAFT, WorkOrderState.CLOSED},
        WorkOrderState.CLOSED: {WorkOrderState.OPEN},
    }
    return dst in allowed.get(src, set())
