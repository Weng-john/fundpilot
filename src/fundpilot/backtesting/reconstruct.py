"""Reconstruct historical inputs that the exchange does not serve retroactively.

Bitfinex serves historical funding *trades*, *candles* and *funding statistics*, but not
historical order books or ticker snapshots. To backtest over the past, ticker rows are
rebuilt from the hourly funding-statistics FRR and stored with ``origin='reconstructed'``.
They carry only the FRR (other ticker fields are unknown) and are excluded from every
backtest unless explicitly allowed, and reports are labelled accordingly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundpilot.core.models import DataOrigin, FundingTicker
from fundpilot.core.time import ensure_utc
from fundpilot.data import repository
from fundpilot.data.models import FundingStatRow


def tickers_from_funding_stats(
    session: Session, *, exchange: str, symbol: str, start: datetime, end: datetime
) -> int:
    """Create reconstructed FRR-only ticker rows from stored funding stats. Idempotent."""
    rows = session.scalars(
        select(FundingStatRow).where(
            FundingStatRow.exchange == exchange,
            FundingStatRow.symbol == symbol,
            FundingStatRow.observed_at >= ensure_utc(start),
            FundingStatRow.observed_at <= ensure_utc(end),
            FundingStatRow.origin == DataOrigin.EXCHANGE_API.value,
            FundingStatRow.frr.is_not(None),
        )
    ).all()
    tickers = [
        FundingTicker(
            exchange=r.exchange,
            symbol=r.symbol,
            observed_at=r.observed_at,
            frr=r.frr,
            bid=None,
            bid_period_days=None,
            bid_size=None,
            ask=None,
            ask_period_days=None,
            ask_size=None,
            daily_change=None,
            daily_change_relative=None,
            last_price=None,
            volume=None,
            high=None,
            low=None,
            frr_amount_available=None,
            origin=DataOrigin.RECONSTRUCTED,
        )
        for r in rows
    ]
    return repository.save_tickers(session, tickers)
