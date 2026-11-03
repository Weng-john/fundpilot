"""SQLAlchemy ORM schema.

Market tables carry ``origin`` so that synthetic or reconstructed rows can never
be mistaken for genuine exchange observations. Unique constraints make every
ingestion path idempotent (``INSERT ... ON CONFLICT DO NOTHING``).

Account tables (wallets, offers, credits, interest) are defined now so the
schema is stable, but are only populated from Phase 3 (paper) / Phase 7 (live).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fundpilot.data.types import ExactDecimal, UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ORIGIN_VALUES = "('exchange_api', 'reconstructed', 'synthetic')"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _origin_column() -> Mapped[str]:
    return mapped_column(String(16), nullable=False, default="exchange_api")


# ---------------------------------------------------------------------------
# Operational metadata
# ---------------------------------------------------------------------------
class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rows_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('running', 'success', 'failed')", name="status"),
        Index("ix_ingestion_runs_job_started", "job", "started_at"),
    )


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    window_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("fingerprint"),
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="severity"),
        Index("ix_dq_dataset_symbol_detected", "dataset", "symbol", "detected_at"),
    )


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
class FundingBookSnapshotRow(Base):
    __tablename__ = "funding_book_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    precision: Mapped[str] = mapped_column(String(8), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    level_count: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = _origin_column()

    levels: Mapped[list[FundingBookLevelRow]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("exchange", "symbol", "precision", "captured_at"),
        CheckConstraint(f"origin IN {ORIGIN_VALUES}", name="origin"),
        Index("ix_book_snap_symbol_time", "exchange", "symbol", "captured_at"),
    )


class FundingBookLevelRow(Base):
    __tablename__ = "funding_book_levels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("funding_book_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    rate: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    count: Mapped[int | None] = mapped_column(Integer)
    offer_id: Mapped[int | None] = mapped_column(BigInteger)

    snapshot: Mapped[FundingBookSnapshotRow] = relationship(back_populates="levels")

    __table_args__ = (
        CheckConstraint("side IN ('bid', 'ask')", name="side"),
        CheckConstraint("CAST(amount AS NUMERIC) >= 0", name="amount_non_negative"),
        Index("ix_book_levels_snapshot", "snapshot_id"),
    )


class FundingTickerRow(Base):
    __tablename__ = "funding_tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    frr: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    bid: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    bid_period_days: Mapped[int | None] = mapped_column(Integer)
    bid_size: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    ask: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    ask_period_days: Mapped[int | None] = mapped_column(Integer)
    ask_size: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    daily_change: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    daily_change_relative: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    last_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    volume: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    high: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    low: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    frr_amount_available: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    origin: Mapped[str] = _origin_column()

    __table_args__ = (
        UniqueConstraint("exchange", "symbol", "observed_at"),
        CheckConstraint(f"origin IN {ORIGIN_VALUES}", name="origin"),
    )


class FundingStatRow(Base):
    __tablename__ = "funding_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    frr_raw: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    frr: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    avg_period_days: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    funding_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    funding_amount_used: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    funding_below_threshold: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    origin: Mapped[str] = _origin_column()

    __table_args__ = (
        UniqueConstraint("exchange", "symbol", "observed_at"),
        CheckConstraint(f"origin IN {ORIGIN_VALUES}", name="origin"),
    )


class FundingCandleRow(Base):
    __tablename__ = "funding_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    candle_key: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    open: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    close: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    high: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    low: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    volume: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    origin: Mapped[str] = _origin_column()

    __table_args__ = (
        UniqueConstraint("exchange", "candle_key", "open_time"),
        CheckConstraint(f"origin IN {ORIGIN_VALUES}", name="origin"),
    )


class FundingTradeRow(Base):
    __tablename__ = "funding_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    amount: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    rate: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = _origin_column()

    __table_args__ = (
        UniqueConstraint("exchange", "symbol", "trade_id"),
        CheckConstraint(f"origin IN {ORIGIN_VALUES}", name="origin"),
        Index("ix_funding_trades_symbol_time", "exchange", "symbol", "executed_at"),
    )


# ---------------------------------------------------------------------------
# Account state (populated from Phase 3 paper trading / Phase 7 live)
# ---------------------------------------------------------------------------
_ACCOUNT_MODES = "('paper', 'live')"


class FundingWalletSnapshotRow(Base):
    __tablename__ = "funding_wallet_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    balance: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    available: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    unsettled_interest: Mapped[Decimal | None] = mapped_column(ExactDecimal())

    __table_args__ = (
        UniqueConstraint("account", "mode", "exchange", "currency", "captured_at"),
        CheckConstraint(f"mode IN {_ACCOUNT_MODES}", name="mode"),
    )


class FundingOfferEventRow(Base):
    __tablename__ = "funding_offer_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_ref: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    event_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    amount: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    amount_orig: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    rate: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    status_raw: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        UniqueConstraint("account", "mode", "exchange", "offer_id", "event", "event_at"),
        CheckConstraint(f"mode IN {_ACCOUNT_MODES}", name="mode"),
        CheckConstraint(
            "event IN ('submitted', 'updated', 'partially_filled', 'filled', "
            "'cancelled', 'rejected')",
            name="event",
        ),
    )


class FundingCreditEventRow(Base):
    __tablename__ = "funding_credit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    credit_id: Mapped[str] = mapped_column(String(64), nullable=False)
    offer_id: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    event_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    amount: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    rate: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    rate_type: Mapped[str | None] = mapped_column(String(16))
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        UniqueConstraint("account", "mode", "exchange", "credit_id", "event", "event_at"),
        CheckConstraint(f"mode IN {_ACCOUNT_MODES}", name="mode"),
        CheckConstraint("event IN ('opened', 'updated', 'closed')", name="event"),
    )


class FundingInterestPaymentRow(Base):
    __tablename__ = "funding_interest_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("account", "mode", "exchange", "external_id"),
        CheckConstraint(f"mode IN {_ACCOUNT_MODES}", name="mode"),
    )


# ---------------------------------------------------------------------------
# Operations: controls, approvals, execution audit, alerts, paper account state
# ---------------------------------------------------------------------------
class ControlFlagRow(Base):
    """Persistent operator switches, e.g. ``emergency_stop``."""

    __tablename__ = "control_flags"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class ApprovalRequestRow(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'executed')", name="status"
        ),
        CheckConstraint(f"mode IN {_ACCOUNT_MODES}", name="mode"),
        Index("ix_approval_status", "account", "status"),
    )


class ExecutionAuditRow(Base):
    """Append-only record of every execution attempt, in every mode (dry-run included)."""

    __tablename__ = "execution_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange_ref: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("mode IN ('dry_run', 'paper', 'live')", name="mode"),
        CheckConstraint("action IN ('place', 'cancel')", name="action"),
        CheckConstraint(
            "outcome IN ('simulated', 'submitted', 'duplicate', 'failed', 'refused')",
            name="outcome",
        ),
        Index("ix_audit_account_at", "account", "at"),
    )


class IdempotencyKeyRow(Base):
    __tablename__ = "execution_idempotency_keys"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    exchange_ref: Mapped[str | None] = mapped_column(String(64))


class AlertRow(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("severity IN ('info', 'warning', 'critical')", name="severity"),
        Index("ix_alerts_at", "at"),
    )


class PaperAccountStateRow(Base):
    """Serialised virtual account so paper trading survives restarts."""

    __tablename__ = "paper_account_state"

    account: Mapped[str] = mapped_column(String(64), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


# ---------------------------------------------------------------------------
# AI supervisory agent (Phase 6)
# ---------------------------------------------------------------------------
class AgentRecommendationRow(Base):
    """Validated configuration proposals. Never applied automatically."""

    __tablename__ = "agent_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # "llm:<model>" | "rules"
    parameter: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_value: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    risk_increasing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        CheckConstraint("status IN ('proposed', 'accepted', 'rejected')", name="status"),
    )


class AgentActivityRow(Base):
    __tablename__ = "agent_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # report | question | tool
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_agent_activity_at", "at"),)
