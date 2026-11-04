"""defi reserve snapshots

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04 17:46:07.877687
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import fundpilot.data.types

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "defi_reserve_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("underlying", sa.String(length=42), nullable=False),
        sa.Column("observed_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("total_supplied", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("total_debt", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("available_liquidity", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("supply_apr", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("borrow_apr", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("reserve_factor", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("supply_cap", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("optimal_usage", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("base_rate", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("slope1", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("slope2", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_frozen", sa.Boolean(), nullable=False),
        sa.Column("is_paused", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "origin IN ('exchange_api', 'reconstructed', 'synthetic')",
            name=op.f("ck_defi_reserve_snapshots_origin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_defi_reserve_snapshots")),
        sa.UniqueConstraint(
            "protocol",
            "chain",
            "symbol",
            "observed_at",
            name=op.f("uq_defi_reserve_snapshots_protocol_chain_symbol_observed_at"),
        ),
    )
    with op.batch_alter_table("defi_reserve_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            "ix_defi_snap_key_time", ["protocol", "chain", "symbol", "observed_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("defi_reserve_snapshots", schema=None) as batch_op:
        batch_op.drop_index("ix_defi_snap_key_time")

    op.drop_table("defi_reserve_snapshots")
