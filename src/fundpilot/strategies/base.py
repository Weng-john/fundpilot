"""Strategy contract.

A strategy receives the *same* standardised :class:`MarketState` and
:class:`~fundpilot.core.portfolio.PortfolioState` in backtests and paper trading
and returns a :class:`Decision`. Strategies may keep internal state across calls
(e.g. rolling statistics) but only ever see data timestamped at or before
``market.as_of``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from fundpilot.core.models import (
    BookSide,
    FundingBookLevel,
    FundingBookSnapshot,
    FundingTicker,
    FundingTrade,
)
from fundpilot.core.portfolio import PortfolioState, RateType


@dataclass(frozen=True, slots=True)
class MarketState:
    as_of: datetime
    symbol: str
    ticker: FundingTicker | None = None
    book: FundingBookSnapshot | None = None
    trades: tuple[FundingTrade, ...] = ()  # recent trades, all executed_at <= as_of
    stale: bool = False  # inputs older than the configured staleness threshold
    # point-in-time external indicators, e.g. {"btc_rv_24h": 0.45} (annualised volatility)
    indicators: Mapping[str, float] = field(default_factory=dict)

    @property
    def frr(self) -> Decimal | None:
        return None if self.ticker is None else self.ticker.frr

    def levels(self, side: BookSide) -> tuple[FundingBookLevel, ...]:
        if self.book is None:
            return ()
        return self.book.side(side)

    def best_bid(self) -> FundingBookLevel | None:
        bids = self.levels(BookSide.BID)
        return max(bids, key=lambda lv: lv.rate) if bids else None

    def best_ask(self) -> FundingBookLevel | None:
        asks = self.levels(BookSide.ASK)
        return min(asks, key=lambda lv: lv.rate) if asks else None


@dataclass(frozen=True, slots=True)
class OfferRequest:
    rate_type: RateType
    rate: Decimal | None  # required for FIXED, must be None for FRR
    period_days: int
    amount: Decimal
    tag: str = ""


@dataclass(frozen=True, slots=True)
class Decision:
    place: tuple[OfferRequest, ...] = ()
    cancel: tuple[str, ...] = ()  # offer ids
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.place and not self.cancel


@runtime_checkable
class Strategy(Protocol):
    @property
    def name(self) -> str: ...

    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision: ...
