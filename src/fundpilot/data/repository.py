"""Idempotent persistence for normalised market data."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import InstrumentedAttribute, Session

from fundpilot.core.models import (
    FundingBookSnapshot,
    FundingCandle,
    FundingStat,
    FundingTicker,
    FundingTrade,
    TradingCandle,
)
from fundpilot.data.models import (
    Base,
    FundingBookLevelRow,
    FundingBookSnapshotRow,
    FundingCandleRow,
    FundingStatRow,
    FundingTickerRow,
    FundingTradeRow,
    TradingCandleRow,
)

_CHUNK = 500


def insert_ignore(session: Session, model: type[Base], rows: Sequence[dict[str, Any]]) -> int:
    """Insert rows, silently skipping those that violate a unique constraint."""
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:  # pragma: no cover
        raise NotImplementedError(f"unsupported dialect: {dialect}")
    pk = model.__table__.primary_key.columns.values()[0]  # type: ignore[attr-defined]
    inserted = 0
    for i in range(0, len(rows), _CHUNK):
        chunk = list(rows[i : i + _CHUNK])
        # RETURNING gives an exact count; DB-API rowcount is unreliable here (-1 on psycopg).
        stmt = (
            postgresql.insert(model).values(chunk).on_conflict_do_nothing().returning(pk)
            if dialect == "postgresql"
            else sqlite.insert(model).values(chunk).on_conflict_do_nothing().returning(pk)
        )
        inserted += len(session.execute(stmt).all())
    return inserted


def save_tickers(session: Session, tickers: Iterable[FundingTicker]) -> int:
    return insert_ignore(session, FundingTickerRow, [t.model_dump() for t in tickers])


def save_funding_stats(session: Session, stats: Iterable[FundingStat]) -> int:
    return insert_ignore(session, FundingStatRow, [s.model_dump() for s in stats])


def save_trades(session: Session, trades: Iterable[FundingTrade]) -> int:
    return insert_ignore(session, FundingTradeRow, [t.model_dump() for t in trades])


def save_candles(session: Session, candles: Iterable[FundingCandle]) -> int:
    return insert_ignore(session, FundingCandleRow, [c.model_dump() for c in candles])


def save_trading_candles(session: Session, candles: Iterable[TradingCandle]) -> int:
    return insert_ignore(session, TradingCandleRow, [c.model_dump() for c in candles])


def save_book_snapshot(session: Session, snapshot: FundingBookSnapshot) -> int:
    """Store a book snapshot with its levels. Returns levels inserted (0 if duplicate)."""
    existing = session.scalar(
        select(FundingBookSnapshotRow.id).where(
            FundingBookSnapshotRow.exchange == snapshot.exchange,
            FundingBookSnapshotRow.symbol == snapshot.symbol,
            FundingBookSnapshotRow.precision == snapshot.precision,
            FundingBookSnapshotRow.captured_at == snapshot.captured_at,
        )
    )
    if existing is not None:
        return 0
    row = FundingBookSnapshotRow(
        exchange=snapshot.exchange,
        symbol=snapshot.symbol,
        precision=snapshot.precision,
        captured_at=snapshot.captured_at,
        level_count=len(snapshot.levels),
        origin=snapshot.origin.value,
        levels=[
            FundingBookLevelRow(
                side=level.side.value,
                rate=level.rate,
                period_days=level.period_days,
                amount=level.amount,
                count=level.count,
                offer_id=level.offer_id,
            )
            for level in snapshot.levels
        ],
    )
    session.add(row)
    session.flush()
    return len(snapshot.levels)


# -- queries ---------------------------------------------------------------
TIME_COLUMNS: dict[str, tuple[Any, InstrumentedAttribute[datetime]]] = {
    "funding_stats": (FundingStatRow, FundingStatRow.observed_at),
    "funding_tickers": (FundingTickerRow, FundingTickerRow.observed_at),
    "funding_trades": (FundingTradeRow, FundingTradeRow.executed_at),
    "funding_candles": (FundingCandleRow, FundingCandleRow.open_time),
    "funding_book_snapshots": (FundingBookSnapshotRow, FundingBookSnapshotRow.captured_at),
}


def _time_column(dataset: str) -> tuple[Any, InstrumentedAttribute[datetime]]:
    try:
        return TIME_COLUMNS[dataset]
    except KeyError:
        raise ValueError(
            f"unknown dataset {dataset!r}; choose from {sorted(TIME_COLUMNS)}"
        ) from None


def latest_timestamp(session: Session, dataset: str, exchange: str, symbol: str) -> datetime | None:
    model, column = _time_column(dataset)
    return session.scalar(
        select(func.max(column)).where(
            model.exchange == exchange,
            model.symbol == symbol,
        )
    )


def timestamps(
    session: Session,
    dataset: str,
    exchange: str,
    symbol: str,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    candle_key: str | None = None,
) -> list[datetime]:
    model, column = _time_column(dataset)
    stmt = select(column).where(
        model.exchange == exchange,
        model.symbol == symbol,
    )
    if candle_key is not None:
        stmt = stmt.where(FundingCandleRow.candle_key == candle_key)
    if start is not None:
        stmt = stmt.where(column >= start)
    if end is not None:
        stmt = stmt.where(column <= end)
    return list(session.scalars(stmt.order_by(column)))


def row_counts(session: Session) -> dict[str, int]:
    return {
        name: session.scalar(select(func.count()).select_from(model)) or 0
        for name, (model, _) in TIME_COLUMNS.items()
    }
