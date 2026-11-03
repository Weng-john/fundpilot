"""Hand-built market states for strategy and simulator tests (synthetic, not market data)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from fundpilot.core.models import (
    BookSide,
    DataOrigin,
    FundingBookLevel,
    FundingBookSnapshot,
    FundingTicker,
    FundingTrade,
)
from fundpilot.strategies.base import MarketState
from tests.conftest import FIXED_NOW

D = Decimal


def ticker(at: datetime, frr: str | None = "0.0002") -> FundingTicker:
    return FundingTicker(
        exchange="bitfinex",
        symbol="fUST",
        observed_at=at,
        frr=None if frr is None else D(frr),
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
        origin=DataOrigin.SYNTHETIC,
    )


def level(side: str, rate: str, amount: str, period: int = 2) -> FundingBookLevel:
    return FundingBookLevel(
        side=BookSide(side), rate=D(rate), period_days=period, amount=D(amount), count=1
    )


def book(at: datetime, levels: Sequence[FundingBookLevel] = ()) -> FundingBookSnapshot:
    return FundingBookSnapshot(
        exchange="bitfinex",
        symbol="fUST",
        precision="P0",
        captured_at=at,
        levels=tuple(levels),
        origin=DataOrigin.SYNTHETIC,
    )


_trade_ids = iter(range(1, 10**9))


def trade(at: datetime, rate: str, amount: str, period: int = 2) -> FundingTrade:
    return FundingTrade(
        exchange="bitfinex",
        symbol="fUST",
        trade_id=next(_trade_ids),
        executed_at=at,
        amount=D(amount),
        rate=D(rate),
        period_days=period,
        origin=DataOrigin.SYNTHETIC,
    )


def state(
    at: datetime,
    *,
    frr: str | None = "0.0002",
    levels: Sequence[FundingBookLevel] = (),
    trades: Sequence[FundingTrade] = (),
    stale: bool = False,
) -> MarketState:
    return MarketState(at, "fUST", ticker(at, frr), book(at, levels), tuple(trades), stale)


def series(
    n: int,
    step: timedelta = timedelta(minutes=1),
    *,
    start: datetime = FIXED_NOW,
    frr: str = "0.0002",
    levels: Sequence[FundingBookLevel] = (),
    trades_per_step: Sequence[tuple[str, str, int]] = (),
) -> list[MarketState]:
    """``n`` states; each step gets trades (rate, amount, period) just before its timestamp."""
    out = []
    for i in range(n):
        at = start + step * i
        tr = [trade(at - timedelta(seconds=1), r, a, p) for r, a, p in trades_per_step] if i else []
        out.append(state(at, frr=frr, levels=levels, trades=tr))
    return out
