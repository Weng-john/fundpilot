"""Build point-in-time :class:`MarketState` sequences from stored observations.

Look-ahead protection: the state at time ``t`` contains only the latest ticker and
book observed at or before ``t`` and trades executed in ``(t - lookback, t]``.
Rows are filtered by ``origin``; by default only genuine exchange observations
(``exchange_api``) are used.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fundpilot.core.models import (
    BookSide,
    DataOrigin,
    FundingBookLevel,
    FundingBookSnapshot,
    FundingTicker,
    FundingTrade,
)
from fundpilot.core.time import ensure_utc
from fundpilot.data.models import FundingBookSnapshotRow, FundingTickerRow, FundingTradeRow
from fundpilot.strategies.base import MarketState

_TICKER_FIELDS = (
    "frr",
    "bid",
    "bid_period_days",
    "bid_size",
    "ask",
    "ask_period_days",
    "ask_size",
    "daily_change",
    "daily_change_relative",
    "last_price",
    "volume",
    "high",
    "low",
    "frr_amount_available",
)


@dataclass(frozen=True, slots=True)
class FeedConfig:
    exchange: str
    symbol: str
    start: datetime
    end: datetime
    step: timedelta = timedelta(minutes=1)
    trade_lookback: timedelta = timedelta(hours=24)
    max_staleness: timedelta = timedelta(minutes=5)
    book_precision: str = "P0"
    origins: Collection[DataOrigin] = (DataOrigin.EXCHANGE_API,)


@dataclass(frozen=True, slots=True)
class FeedProvenance:
    origins: tuple[str, ...]
    tickers: int
    books: int
    trades: int
    first_observation: datetime | None
    last_observation: datetime | None


class MarketFeed:
    """In-memory, point-in-time view over loaded observations."""

    def __init__(
        self,
        config: FeedConfig,
        tickers: list[FundingTicker],
        books: list[FundingBookSnapshot],
        trades: list[FundingTrade],
    ) -> None:
        if config.step <= timedelta(0):
            raise ValueError("step must be positive")
        self.config = config
        self._tickers = sorted(tickers, key=lambda t: t.observed_at)
        self._ticker_ts = [t.observed_at for t in self._tickers]
        self._books = sorted(books, key=lambda b: b.captured_at)
        self._book_ts = [b.captured_at for b in self._books]
        self._trades = sorted(trades, key=lambda t: t.executed_at)
        self._trade_ts = [t.executed_at for t in self._trades]

    @property
    def provenance(self) -> FeedProvenance:
        times = self._ticker_ts + self._book_ts + self._trade_ts
        return FeedProvenance(
            origins=tuple(sorted(str(o) for o in self.config.origins)),
            tickers=len(self._tickers),
            books=len(self._books),
            trades=len(self._trades),
            first_observation=min(times) if times else None,
            last_observation=max(times) if times else None,
        )

    def state_at(self, t: datetime) -> MarketState:
        t = ensure_utc(t)
        cfg = self.config
        i = bisect_right(self._ticker_ts, t)
        ticker = self._tickers[i - 1] if i else None
        j = bisect_right(self._book_ts, t)
        book = self._books[j - 1] if j else None
        hi = bisect_right(self._trade_ts, t)
        lo = bisect_right(self._trade_ts, t - cfg.trade_lookback)
        stale = (
            ticker is None
            or book is None
            or t - ticker.observed_at > cfg.max_staleness
            or t - book.captured_at > cfg.max_staleness
        )
        return MarketState(t, cfg.symbol, ticker, book, tuple(self._trades[lo:hi]), stale)

    def __iter__(self) -> Iterator[MarketState]:
        t = ensure_utc(self.config.start)
        end = ensure_utc(self.config.end)
        while t <= end:
            yield self.state_at(t)
            t += self.config.step


def load_feed(session: Session, config: FeedConfig) -> MarketFeed:
    origins = [str(o) for o in config.origins]
    start, end = ensure_utc(config.start), ensure_utc(config.end)
    warmup = start - max(config.max_staleness, config.trade_lookback)

    ticker_rows = session.scalars(
        select(FundingTickerRow).where(
            FundingTickerRow.exchange == config.exchange,
            FundingTickerRow.symbol == config.symbol,
            FundingTickerRow.observed_at >= warmup,
            FundingTickerRow.observed_at <= end,
            FundingTickerRow.origin.in_(origins),
        )
    ).all()
    tickers = [
        FundingTicker(
            exchange=r.exchange,
            symbol=r.symbol,
            observed_at=r.observed_at,
            origin=DataOrigin(r.origin),
            **{f: getattr(r, f) for f in _TICKER_FIELDS},
        )
        for r in ticker_rows
    ]
    book_rows = session.scalars(
        select(FundingBookSnapshotRow)
        .options(selectinload(FundingBookSnapshotRow.levels))
        .where(
            FundingBookSnapshotRow.exchange == config.exchange,
            FundingBookSnapshotRow.symbol == config.symbol,
            FundingBookSnapshotRow.precision == config.book_precision,
            FundingBookSnapshotRow.captured_at >= warmup,
            FundingBookSnapshotRow.captured_at <= end,
            FundingBookSnapshotRow.origin.in_(origins),
        )
    ).all()
    books = [
        FundingBookSnapshot(
            exchange=r.exchange,
            symbol=r.symbol,
            precision=r.precision,
            captured_at=r.captured_at,
            origin=DataOrigin(r.origin),
            levels=tuple(
                FundingBookLevel(
                    side=BookSide(lv.side),
                    rate=lv.rate,
                    period_days=lv.period_days,
                    amount=lv.amount,
                    count=lv.count,
                    offer_id=lv.offer_id,
                )
                for lv in r.levels
            ),
        )
        for r in book_rows
    ]
    trade_rows = session.scalars(
        select(FundingTradeRow).where(
            FundingTradeRow.exchange == config.exchange,
            FundingTradeRow.symbol == config.symbol,
            FundingTradeRow.executed_at >= warmup,
            FundingTradeRow.executed_at <= end,
            FundingTradeRow.origin.in_(origins),
        )
    ).all()
    trades = [
        FundingTrade(
            exchange=r.exchange,
            symbol=r.symbol,
            trade_id=r.trade_id,
            executed_at=r.executed_at,
            amount=r.amount,
            rate=r.rate,
            period_days=r.period_days,
            origin=DataOrigin(r.origin),
        )
        for r in trade_rows
    ]
    return MarketFeed(config, tickers, books, trades)
