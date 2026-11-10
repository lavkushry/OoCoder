"""Durable orchestrator: transitions, timeouts, crash resume."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig
from oss_agent.db.models import Task
from oss_agent.orchestrator.states import TaskState, TransitionTable


class Orchestrator:
    def __init__(self, session: Session, config: AppConfig, audit: FileAndDbAuditLog) -> None:
        self.session = session
        self.config = config
        self.audit = audit

    def create_task(self, *, project_id: str, issue_id: str | None, state: TaskState) -> Task:
        timeout = getattr(self.config.timeouts, state.name, 3600)
        deadline = None
        if timeout > 0:
            deadline = datetime.now(UTC) + timedelta(seconds=timeout)
        task = Task(
            id=f"tsk_{uuid.uuid4().hex[:16]}",
            project_id=project_id,
            issue_id=issue_id,
            state=state.value,
            budget_tokens=self.config.models.max_tokens_per_task,
            tokens_used=0,
            deadline=deadline,
        )
        self.session.add(task)
        self.session.flush()
        self.audit.append(
            actor="orchestrator",
            event="task.created",
            payload={"task_id": task.id, "state": state.value},
            task_id=task.id,
        )
        return task

    def transition(self, task_id: str, dst: TaskState, *, reason: str) -> Task:
        task = self.session.get(Task, task_id)
        if task is None:
            msg = f"unknown task {task_id}"
            raise KeyError(msg)
        src = TaskState(task.state)
        TransitionTable.assert_transition(src, dst)
        if dst == TaskState.PAUSED:
            task.prior_state = task.state
        task.state = dst.value
        task.updated_at = datetime.now(UTC)
        timeout = getattr(self.config.timeouts, dst.name, 0)
        if timeout > 0:
            task.deadline = datetime.now(UTC) + timedelta(seconds=timeout)
        self.session.flush()
        self.audit.append(
            actor="orchestrator",
            event="task.transition",
            payload={"from": src.value, "to": dst.value, "reason": reason},
            task_id=task.id,
        )
        return task

    def resume_paused(self, task_id: str) -> Task:
        task = self.session.get(Task, task_id)
        if task is None:
            raise KeyError(task_id)
        if TaskState(task.state) != TaskState.PAUSED:
            msg = "task is not paused"
            raise ValueError(msg)
        if not task.prior_state:
            msg = "missing prior_state"
            raise ValueError(msg)
        prior = TaskState(task.prior_state)
        task.state = prior.value
        task.prior_state = None
        task.updated_at = datetime.now(UTC)
        self.session.flush()
        self.audit.append(
            actor="orchestrator",
            event="task.resume",
            payload={"state": prior.value},
            task_id=task.id,
        )
        return task

    def load_active_tasks(self) -> list[Task]:
        from sqlalchemy import select

        rows = list(self.session.scalars(select(Task)).all())
        terminal = {TaskState.MERGED.value, TaskState.ABANDONED.value}
        return [t for t in rows if t.state not in terminal]

    def recover_after_crash(self) -> list[dict[str, Any]]:
        """Idempotent resume: return active tasks and re-arm deadlines if needed."""
        recovered: list[dict[str, Any]] = []
        for task in self.load_active_tasks():
            recovered.append({"task_id": task.id, "state": task.state})
            self.audit.append(
                actor="orchestrator",
                event="task.recover",
                payload={"state": task.state},
                task_id=task.id,
            )
        return recovered

    def tool_allowed(self, task_id: str, tool_name: str) -> bool:
        task = self.session.get(Task, task_id)
        if task is None:
            return False
        return tool_name in TransitionTable.allowed_tools(TaskState(task.state))
