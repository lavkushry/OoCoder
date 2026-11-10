"""SQLAlchemy models. SQLite first; types chosen for Postgres portability."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


metadata = Base.metadata


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ai_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    issues: Mapped[list[Issue]] = relationship(back_populates="project")
    flakes: Mapped[list[Flake]] = relationship(back_populates="project")


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(64), default="discovered")
    url: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="issues")
    tasks: Mapped[list[Task]] = relationship(back_populates="issue")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_id: Mapped[str | None] = mapped_column(ForeignKey("issues.id"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_state: Mapped[str | None] = mapped_column(String(64))  # for PAUSED resume
    budget_tokens: Mapped[int] = mapped_column(Integer, default=200_000)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    issue: Mapped[Issue | None] = relationship(back_populates="tasks")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="task")
    actions: Mapped[list[Action]] = relationship(back_populates="task")
    review_items: Mapped[list[ReviewItem]] = relationship(back_populates="task")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # True only when bytes came from sandbox capture, never model text.
    from_sandbox: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="artifacts")


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    draft: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # pending | approved | sent | rejected
    status: Mapped[str] = mapped_column(String(32), default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_id: Mapped[str | None] = mapped_column(String(128))
    evidence_artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="actions")


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    source_comment: Mapped[str] = mapped_column(Text, nullable=False)
    class_: Mapped[str] = mapped_column("class", String(32), nullable=False)
    draft_reply: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="review_items")


class Flake(Base):
    __tablename__ = "flakes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    test_name: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="flakes")


class AuditLogEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RateCounter(Base):
    __tablename__ = "rate_counters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0)


# --- SuperPlane-inspired factory domain ---


class Factory(Base):
    __tablename__ = "factories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list[FactoryLine]] = relationship(back_populates="factory")
    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="factory")


class FactoryLine(Base):
    __tablename__ = "factory_lines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Ordered step definitions: [{name, app}, ...]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    factory: Mapped[Factory] = relationship(back_populates="lines")


class WorkOrder(Base):
    __tablename__ = "factory_work_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(32), default="draft")
    result: Mapped[str | None] = mapped_column(String(32))
    project_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    factory: Mapped[Factory] = relationship(back_populates="work_orders")
    executions: Mapped[list[WorkOrderExecution]] = relationship(back_populates="work_order")
    artifacts: Mapped[list[WorkOrderArtifact]] = relationship(back_populates="work_order")
    events: Mapped[list[WorkOrderEvent]] = relationship(back_populates="work_order")


class WorkOrderExecution(Base):
    __tablename__ = "factory_work_order_executions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"), nullable=False)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("factory_work_orders.id"), nullable=False)
    line_id: Mapped[str] = mapped_column(ForeignKey("factory_lines.id"), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    result: Mapped[str | None] = mapped_column(String(32))
    task_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    work_order: Mapped[WorkOrder] = relationship(back_populates="executions")


class WorkOrderArtifact(Base):
    __tablename__ = "factory_work_order_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("factory_work_orders.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # markdown|branch|link|pr
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    key: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    work_order: Mapped[WorkOrder] = relationship(back_populates="artifacts")


class WorkOrderEvent(Base):
    __tablename__ = "factory_work_order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("factory_work_orders.id"), nullable=False)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    work_order: Mapped[WorkOrder] = relationship(back_populates="events")
