"""factory tables (SuperPlane-inspired)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "factories",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "factory_lines",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("factory_id", sa.String(64), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "factory_work_orders",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("factory_id", sa.String(64), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("result", sa.String(32)),
        sa.Column("project_id", sa.String(64)),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "factory_work_order_executions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("factory_id", sa.String(64), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column(
            "work_order_id",
            sa.String(64),
            sa.ForeignKey("factory_work_orders.id"),
            nullable=False,
        ),
        sa.Column("line_id", sa.String(64), sa.ForeignKey("factory_lines.id"), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", sa.String(32)),
        sa.Column("task_id", sa.String(64)),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "factory_work_order_artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "work_order_id",
            sa.String(64),
            sa.ForeignKey("factory_work_orders.id"),
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("key", sa.String(512)),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "factory_work_order_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "work_order_id",
            sa.String(64),
            sa.ForeignKey("factory_work_orders.id"),
            nullable=False,
        ),
        sa.Column("event", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("factory_work_order_events")
    op.drop_table("factory_work_order_artifacts")
    op.drop_table("factory_work_order_executions")
    op.drop_table("factory_work_orders")
    op.drop_table("factory_lines")
    op.drop_table("factories")
