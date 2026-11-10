"""Factory CRUD, work-order lifecycle, dispatch — SuperPlane-aligned."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from oss_agent.db.models import (
    Factory,
    FactoryLine,
    WorkOrder,
    WorkOrderArtifact,
    WorkOrderEvent,
    WorkOrderExecution,
)
from oss_agent.factory.lines import OSS_CONTRIBUTION_LINE_NAME, OSS_CONTRIBUTION_STEPS
from oss_agent.factory.states import (
    CLOSE_RESULTS,
    CloseResult,
    ExecutionResult,
    ExecutionStatus,
    WorkOrderState,
    can_transition,
)


class FactoryError(RuntimeError):
    pass


class FactoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_factory(
        self,
        *,
        key: str,
        name: str,
        description: str = "",
        with_default_line: bool = True,
    ) -> Factory:
        factory = Factory(
            id=f"fac_{uuid.uuid4().hex[:12]}",
            key=key,
            name=name,
            description=description,
        )
        self.session.add(factory)
        self.session.flush()
        if with_default_line:
            self.create_line(
                factory_id=factory.id,
                name=OSS_CONTRIBUTION_LINE_NAME,
                steps=list(OSS_CONTRIBUTION_STEPS),
            )
            self.session.refresh(factory)
        return factory

    def create_line(
        self,
        *,
        factory_id: str,
        name: str,
        steps: list[dict[str, Any]],
    ) -> FactoryLine:
        line = FactoryLine(
            id=f"line_{uuid.uuid4().hex[:12]}",
            factory_id=factory_id,
            name=name,
            steps=steps,
        )
        self.session.add(line)
        self.session.flush()
        return line

    def create_work_order(
        self,
        *,
        factory_id: str,
        title: str,
        description: str = "",
        project_id: str | None = None,
        source: dict[str, Any] | None = None,
        created_by: str = "system",
    ) -> WorkOrder:
        number = self._next_number(factory_id)
        order = WorkOrder(
            id=f"wo_{uuid.uuid4().hex[:12]}",
            factory_id=factory_id,
            number=number,
            title=title,
            description=description,
            state=WorkOrderState.DRAFT.value,
            project_id=project_id,
            source=source or {},
            created_by=created_by,
        )
        self.session.add(order)
        self.session.flush()
        self._event(
            order.id,
            "order.status.updated",
            {
                "fromState": None,
                "toState": WorkOrderState.DRAFT.value,
                "fromResult": None,
                "toResult": None,
            },
        )
        return order

    def update_status(
        self,
        order_id: str,
        *,
        state: WorkOrderState,
        result: CloseResult | None = None,
        actor: str = "system",
    ) -> WorkOrder:
        order = self.session.get(WorkOrder, order_id)
        if order is None:
            raise FactoryError(f"unknown work order {order_id}")
        src = WorkOrderState(order.state)
        if not can_transition(src, state) and not (src == state):
            raise FactoryError(f"illegal status {src} -> {state}")
        if state == WorkOrderState.CLOSED:
            if result is None:
                raise FactoryError("close requires result")
            if result not in CLOSE_RESULTS[src]:
                raise FactoryError(f"result {result} invalid from {src}")
        from_result = order.result
        order.state = state.value
        order.result = result.value if result else None
        if state == WorkOrderState.OPEN:
            order.result = None
        order.updated_at = datetime.now(UTC)
        self.session.flush()
        self._event(
            order.id,
            "order.status.updated",
            {
                "fromState": src.value,
                "toState": state.value,
                "fromResult": from_result,
                "toResult": order.result,
                "actor": actor,
            },
        )
        return order

    def dispatch(self, order_id: str, *, line_name: str) -> WorkOrderExecution:
        order = self.session.get(WorkOrder, order_id)
        if order is None:
            raise FactoryError(f"unknown work order {order_id}")
        if WorkOrderState(order.state) not in {WorkOrderState.DRAFT, WorkOrderState.OPEN}:
            raise FactoryError("dispatch only from draft or open")
        if self._active_execution(order_id) is not None:
            raise FactoryError("active execution already running")
        line = self._line_by_name(order.factory_id, line_name)
        if not line.steps:
            raise FactoryError("line has no steps")
        if WorkOrderState(order.state) == WorkOrderState.DRAFT:
            self.update_status(order_id, state=WorkOrderState.OPEN, actor="dispatch")
        step = line.steps[0]
        return self._start_execution(order, line, step_index=0, step=step)

    def advance_or_fail(
        self,
        execution_id: str,
        *,
        passed: bool,
        detail: dict[str, Any] | None = None,
    ) -> WorkOrderExecution | None:
        execution = self.session.get(WorkOrderExecution, execution_id)
        if execution is None:
            raise FactoryError(f"unknown execution {execution_id}")
        execution.status = ExecutionStatus.FINISHED.value
        execution.result = ExecutionResult.PASS.value if passed else ExecutionResult.FAIL.value
        execution.finished_at = datetime.now(UTC)
        execution.detail = {**(execution.detail or {}), **(detail or {})}
        self.session.flush()
        self._event(
            execution.work_order_id,
            "order.execution.finished",
            {
                "execution_id": execution.id,
                "step": execution.step_name,
                "result": execution.result,
            },
        )
        if not passed:
            return None
        order = self.session.get(WorkOrder, execution.work_order_id)
        line = self.session.get(FactoryLine, execution.line_id)
        if order is None or line is None:
            return None
        next_index = execution.step_index + 1
        if next_index >= len(line.steps):
            return None
        return self._start_execution(
            order, line, step_index=next_index, step=line.steps[next_index]
        )

    def add_artifact(
        self,
        order_id: str,
        *,
        artifact_type: str,
        data: dict[str, Any],
        key: str | None = None,
        created_by: str = "system",
    ) -> WorkOrderArtifact:
        if artifact_type == "markdown" and "body" not in data:
            raise FactoryError("markdown artifact requires data.body")
        if artifact_type in {"link", "pr"} and "url" in data:
            url = str(data["url"])
            if not url.startswith(("http://", "https://")):
                raise FactoryError("artifact url must be http(s)")
        art = WorkOrderArtifact(
            id=f"woa_{uuid.uuid4().hex[:12]}",
            work_order_id=order_id,
            type=artifact_type,
            data=data,
            key=key,
            created_by=created_by,
        )
        self.session.add(art)
        self.session.flush()
        self._event(
            order_id,
            "order.artifact.added",
            {"artifact_id": art.id, "type": artifact_type, "data": data},
        )
        return art

    def list_orders(self, factory_id: str) -> list[WorkOrder]:
        stmt = (
            select(WorkOrder).where(WorkOrder.factory_id == factory_id).order_by(WorkOrder.number)
        )
        return list(self.session.scalars(stmt).all())

    def _start_execution(
        self,
        order: WorkOrder,
        line: FactoryLine,
        *,
        step_index: int,
        step: dict[str, Any],
    ) -> WorkOrderExecution:
        execution = WorkOrderExecution(
            id=f"wox_{uuid.uuid4().hex[:12]}",
            factory_id=order.factory_id,
            work_order_id=order.id,
            line_id=line.id,
            step_index=step_index,
            step_name=str(step["name"]),
            status=ExecutionStatus.RUNNING.value,
            detail={"app": step.get("app")},
        )
        self.session.add(execution)
        self.session.flush()
        self._event(
            order.id,
            "order.execution.started",
            {
                "execution_id": execution.id,
                "line": line.name,
                "step": execution.step_name,
                "step_index": step_index,
            },
        )
        return execution

    def _active_execution(self, order_id: str) -> WorkOrderExecution | None:
        active = {ExecutionStatus.PENDING.value, ExecutionStatus.RUNNING.value}
        stmt = select(WorkOrderExecution).where(
            WorkOrderExecution.work_order_id == order_id,
            WorkOrderExecution.status.in_(active),
        )
        return self.session.scalars(stmt).first()

    def _line_by_name(self, factory_id: str, name: str) -> FactoryLine:
        stmt = select(FactoryLine).where(
            FactoryLine.factory_id == factory_id,
            FactoryLine.name == name,
        )
        line = self.session.scalars(stmt).first()
        if line is None:
            raise FactoryError(f"unknown line {name}")
        return line

    def _next_number(self, factory_id: str) -> int:
        stmt = select(func.coalesce(func.max(WorkOrder.number), 0)).where(
            WorkOrder.factory_id == factory_id
        )
        return int(self.session.scalar(stmt) or 0) + 1

    def _event(self, order_id: str, event: str, payload: dict[str, Any]) -> None:
        self.session.add(WorkOrderEvent(work_order_id=order_id, event=event, payload=payload))
        self.session.flush()
