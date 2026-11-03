"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-03 06:30:42.791735
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import fundpilot.data.types

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_quality_issues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("detected_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("window_start", fundpilot.data.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("window_end", fundpilot.data.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name=op.f("ck_data_quality_issues_severity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_quality_issues")),
        sa.UniqueConstraint("fingerprint", name=op.f("uq_data_quality_issues_fingerprint")),
    )
    with op.batch_alter_table("data_quality_issues", schema=None) as batch_op:
        batch_op.create_index(
            "ix_dq_dataset_symbol_detected", ["dataset", "symbol", "detected_at"], unique=False
        )

    op.create_table(
        "funding_book_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("precision", sa.String(length=8), nullable=False),
        sa.Column("captured_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("level_count", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "origin IN ('exchange_api', 'reconstructed', 'synthetic')",
            name=op.f("ck_funding_book_snapshots_origin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_book_snapshots")),
        sa.UniqueConstraint(
            "exchange",
            "symbol",
            "precision",
            "captured_at",
            name=op.f("uq_funding_book_snapshots_exchange_symbol_precision_captured_at"),
        ),
    )
    with op.batch_alter_table("funding_book_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            "ix_book_snap_symbol_time", ["exchange", "symbol", "captured_at"], unique=False
        )

    op.create_table(
        "funding_candles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("candle_key", sa.String(length=64), nullable=False),
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
            name=op.f("ck_funding_candles_origin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_candles")),
        sa.UniqueConstraint(
            "exchange",
            "candle_key",
            "open_time",
            name=op.f("uq_funding_candles_exchange_candle_key_open_time"),
        ),
    )
    op.create_table(
        "funding_credit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("credit_id", sa.String(length=64), nullable=False),
        sa.Column("offer_id", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("event_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("amount", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("rate", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("rate_type", sa.String(length=16), nullable=True),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("opened_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "event IN ('opened', 'updated', 'closed')", name=op.f("ck_funding_credit_events_event")
        ),
        sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f("ck_funding_credit_events_mode")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_credit_events")),
        sa.UniqueConstraint(
            "account",
            "mode",
            "exchange",
            "credit_id",
            "event",
            "event_at",
            name=op.f("uq_funding_credit_events_account_mode_exchange_credit_id_event_event_at"),
        ),
    )
    op.create_table(
        "funding_interest_payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("paid_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("net_amount", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "mode IN ('paper', 'live')", name=op.f("ck_funding_interest_payments_mode")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_interest_payments")),
        sa.UniqueConstraint(
            "account",
            "mode",
            "exchange",
            "external_id",
            name=op.f("uq_funding_interest_payments_account_mode_exchange_external_id"),
        ),
    )
    op.create_table(
        "funding_offer_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("offer_id", sa.String(length=64), nullable=False),
        sa.Column("client_ref", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("event_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("amount", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("amount_orig", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("rate", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("status_raw", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "event IN ('submitted', 'updated', 'partially_filled', 'filled', 'cancelled', 'rejected')",
            name=op.f("ck_funding_offer_events_event"),
        ),
        sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f("ck_funding_offer_events_mode")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_offer_events")),
        sa.UniqueConstraint(
            "account",
            "mode",
            "exchange",
            "offer_id",
            "event",
            "event_at",
            name=op.f("uq_funding_offer_events_account_mode_exchange_offer_id_event_event_at"),
        ),
    )
    op.create_table(
        "funding_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("observed_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("frr_raw", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("frr", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("avg_period_days", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("funding_amount", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("funding_amount_used", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("funding_below_threshold", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "origin IN ('exchange_api', 'reconstructed', 'synthetic')",
            name=op.f("ck_funding_stats_origin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_stats")),
        sa.UniqueConstraint(
            "exchange",
            "symbol",
            "observed_at",
            name=op.f("uq_funding_stats_exchange_symbol_observed_at"),
        ),
    )
    op.create_table(
        "funding_tickers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("observed_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("frr", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("bid", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("bid_period_days", sa.Integer(), nullable=True),
        sa.Column("bid_size", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("ask", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("ask_period_days", sa.Integer(), nullable=True),
        sa.Column("ask_size", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("daily_change", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("daily_change_relative", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("last_price", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("volume", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("high", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("low", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("frr_amount_available", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "origin IN ('exchange_api', 'reconstructed', 'synthetic')",
            name=op.f("ck_funding_tickers_origin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_tickers")),
        sa.UniqueConstraint(
            "exchange",
            "symbol",
            "observed_at",
            name=op.f("uq_funding_tickers_exchange_symbol_observed_at"),
        ),
    )
    op.create_table(
        "funding_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("trade_id", sa.BigInteger(), nullable=False),
        sa.Column("executed_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("amount", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("rate", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "origin IN ('exchange_api', 'reconstructed', 'synthetic')",
            name=op.f("ck_funding_trades_origin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_trades")),
        sa.UniqueConstraint(
            "exchange",
            "symbol",
            "trade_id",
            name=op.f("uq_funding_trades_exchange_symbol_trade_id"),
        ),
    )
    with op.batch_alter_table("funding_trades", schema=None) as batch_op:
        batch_op.create_index(
            "ix_funding_trades_symbol_time", ["exchange", "symbol", "executed_at"], unique=False
        )

    op.create_table(
        "funding_wallet_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("captured_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("balance", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("available", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.Column("unsettled_interest", fundpilot.data.types.ExactDecimal(), nullable=True),
        sa.CheckConstraint(
            "mode IN ('paper', 'live')", name=op.f("ck_funding_wallet_snapshots_mode")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_wallet_snapshots")),
        sa.UniqueConstraint(
            "account",
            "mode",
            "exchange",
            "currency",
            "captured_at",
            name=op.f("uq_funding_wallet_snapshots_account_mode_exchange_currency_captured_at"),
        ),
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("started_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("finished_at", fundpilot.data.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rows_fetched", sa.Integer(), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name=op.f("ck_ingestion_runs_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_runs")),
    )
    with op.batch_alter_table("ingestion_runs", schema=None) as batch_op:
        batch_op.create_index("ix_ingestion_runs_job_started", ["job", "started_at"], unique=False)

    op.create_table(
        "funding_book_levels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("rate", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("amount", fundpilot.data.types.ExactDecimal(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=True),
        sa.Column("offer_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("side IN ('bid', 'ask')", name=op.f("ck_funding_book_levels_side")),
        sa.CheckConstraint(
            "CAST(amount AS NUMERIC) >= 0", name=op.f("ck_funding_book_levels_amount_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["funding_book_snapshots.id"],
            name=op.f("fk_funding_book_levels_snapshot_id_funding_book_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_book_levels")),
    )
    with op.batch_alter_table("funding_book_levels", schema=None) as batch_op:
        batch_op.create_index("ix_book_levels_snapshot", ["snapshot_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("funding_book_levels", schema=None) as batch_op:
        batch_op.drop_index("ix_book_levels_snapshot")

    op.drop_table("funding_book_levels")
    with op.batch_alter_table("ingestion_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_ingestion_runs_job_started")

    op.drop_table("ingestion_runs")
    op.drop_table("funding_wallet_snapshots")
    with op.batch_alter_table("funding_trades", schema=None) as batch_op:
        batch_op.drop_index("ix_funding_trades_symbol_time")

    op.drop_table("funding_trades")
    op.drop_table("funding_tickers")
    op.drop_table("funding_stats")
    op.drop_table("funding_offer_events")
    op.drop_table("funding_interest_payments")
    op.drop_table("funding_credit_events")
    op.drop_table("funding_candles")
    with op.batch_alter_table("funding_book_snapshots", schema=None) as batch_op:
        batch_op.drop_index("ix_book_snap_symbol_time")

    op.drop_table("funding_book_snapshots")
    with op.batch_alter_table("data_quality_issues", schema=None) as batch_op:
        batch_op.drop_index("ix_dq_dataset_symbol_detected")

    op.drop_table("data_quality_issues")
