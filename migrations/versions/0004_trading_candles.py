"""trading candles

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03 07:48:01.874858
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import fundpilot.data.types


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_candles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("open_time", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("open", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("close", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("high", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("low", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("volume", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "origin IN ('exchange_api', 'reconstructed', 'synthetic')",
            name=op.f("ck_trading_candles_origin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trading_candles")),
        sa.UniqueConstraint(
            "exchange",
            "symbol",
            "timeframe",
            "open_time",
            name=op.f("uq_trading_candles_exchange_symbol_timeframe_open_time"),
        ),
    )


def downgrade() -> None:
    op.drop_table("trading_candles")
