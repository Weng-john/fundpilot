"""operations tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03 07:04:33.687898
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import fundpilot.data.types

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name=op.f("ck_alerts_severity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.create_index("ix_alerts_at", ["at"], unique=False)

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("created_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("expires_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f("ck_approval_requests_mode")),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'executed')",
            name=op.f("ck_approval_requests_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
    )
    with op.batch_alter_table("approval_requests", schema=None) as batch_op:
        batch_op.create_index("ix_approval_status", ["account", "status"], unique=False)

    op.create_table(
        "control_flags",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("updated_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_control_flags")),
    )
    op.create_table(
        "execution_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("exchange_ref", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "action IN ('place', 'cancel')", name=op.f("ck_execution_audit_log_action")
        ),
        sa.CheckConstraint(
            "mode IN ('dry_run', 'paper', 'live')", name=op.f("ck_execution_audit_log_mode")
        ),
        sa.CheckConstraint(
            "outcome IN ('simulated', 'submitted', 'duplicate', 'failed', 'refused')",
            name=op.f("ck_execution_audit_log_outcome"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_audit_log")),
    )
    with op.batch_alter_table("execution_audit_log", schema=None) as batch_op:
        batch_op.create_index("ix_audit_account_at", ["account", "at"], unique=False)

    op.create_table(
        "execution_idempotency_keys",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("created_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("exchange_ref", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_execution_idempotency_keys")),
    )
    op.create_table(
        "paper_account_state",
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("updated_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("account", name=op.f("pk_paper_account_state")),
    )


def downgrade() -> None:
    op.drop_table("paper_account_state")
    op.drop_table("execution_idempotency_keys")
    with op.batch_alter_table("execution_audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_account_at")

    op.drop_table("execution_audit_log")
    op.drop_table("control_flags")
    with op.batch_alter_table("approval_requests", schema=None) as batch_op:
        batch_op.drop_index("ix_approval_status")

    op.drop_table("approval_requests")
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_index("ix_alerts_at")

    op.drop_table("alerts")
