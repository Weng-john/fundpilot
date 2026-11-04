"""cex earn rates

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04 18:12:56.074328
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import fundpilot.data.types

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cex_earn_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("product", sa.String(length=48), nullable=False),
        sa.Column("ccy", sa.String(length=16), nullable=False),
        sa.Column("observed_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("borrow_rate", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("lending_rate", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "origin IN ('exchange_api', 'reconstructed', 'synthetic')",
            name=op.f("ck_cex_earn_rates_origin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cex_earn_rates")),
        sa.UniqueConstraint(
            "venue",
            "product",
            "ccy",
            "observed_at",
            name=op.f("uq_cex_earn_rates_venue_product_ccy_observed_at"),
        ),
    )


def downgrade() -> None:
    op.drop_table("cex_earn_rates")
