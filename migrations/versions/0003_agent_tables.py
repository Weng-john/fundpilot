"""agent tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03 07:32:25.728691
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import fundpilot.data.types

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_activity",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_activity")),
    )
    with op.batch_alter_table("agent_activity", schema=None) as batch_op:
        batch_op.create_index("ix_agent_activity_at", ["at"], unique=False)

    op.create_table(
        "agent_recommendations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("parameter", sa.String(length=64), nullable=False),
        sa.Column("proposed_value", sa.String(length=64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("risk_increasing", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected')",
            name=op.f("ck_agent_recommendations_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_recommendations")),
    )


def downgrade() -> None:
    op.drop_table("agent_recommendations")
    with op.batch_alter_table("agent_activity", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_activity_at")

    op.drop_table("agent_activity")
