"""In-memory fake adapter for tests. Generates clearly synthetic records."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from fundpilot.core.models import (
    BookSide,
    DataOrigin,
    FundingBookLevel,
    FundingBookSnapshot,
    FundingCandle,
    FundingStat,
    FundingTicker,
    FundingTrade,
)
from tests.conftest import FIXED_NOW


def make_trades(n: int, start: datetime, step: timedelta) -> list[FundingTrade]:
    return [
        FundingTrade(
            exchange="bitfinex",
            symbol="fUST",
            trade_id=i + 1,
            executed_at=start + step * i,
            amount=Decimal(100),
            rate=Decimal("0.0002"),
            period_days=2,
            origin=DataOrigin.SYNTHETIC,
        )
        for i in range(n)
    ]


class FakeAdapter:
    exchange = "bitfinex"

    def __init__(
        self,
        *,
        trades: list[FundingTrade] | None = None,
        fail: set[str] | None = None,
        now: datetime = FIXED_NOW,
    ) -> None:
        self.trades = trades or []
        self.fail = fail or set()
        self.now = now

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} failed")

    def is_operational(self) -> bool:
        return True

    def get_funding_book(
        self, symbol: str, precision: str = "P0", length: int = 100
    ) -> FundingBookSnapshot:
        self._maybe_fail("book")
        return FundingBookSnapshot(
            exchange="bitfinex",
            symbol=symbol,
            precision=precision,
            captured_at=self.now,
            origin=DataOrigin.SYNTHETIC,
            levels=(
                FundingBookLevel(
                    side=BookSide.ASK,
                    rate=Decimal("0.0002"),
                    period_days=2,
                    amount=Decimal(10),
                    count=1,
                ),
            ),
        )

    def get_funding_raw_book(self, symbol: str, length: int = 100) -> FundingBookSnapshot:
        self._maybe_fail("raw_book")
        return FundingBookSnapshot(
            exchange="bitfinex",
            symbol=symbol,
            precision="R0",
            captured_at=self.now,
            origin=DataOrigin.SYNTHETIC,
            levels=(
                FundingBookLevel(
                    side=BookSide.BID,
                    rate=Decimal("0.0001"),
                    period_days=30,
                    amount=Decimal(10),
                    offer_id=1,
                ),
            ),
        )

    def get_funding_ticker(self, symbol: str) -> FundingTicker:
        self._maybe_fail("ticker")
        return FundingTicker(
            exchange="bitfinex",
            symbol=symbol,
            observed_at=self.now,
            frr=Decimal("0.0002"),
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

    def get_funding_stats(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingStat]:
        self._maybe_fail("stats")
        return [
            FundingStat(
                exchange="bitfinex",
                symbol=symbol,
                observed_at=self.now,
                frr_raw=Decimal(1),
                frr=Decimal(1) * 365,
                avg_period_days=None,
                funding_amount=Decimal(10),
                funding_amount_used=Decimal(5),
                funding_below_threshold=None,
                origin=DataOrigin.SYNTHETIC,
            )
        ]

    def get_funding_trades(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingTrade]:
        self._maybe_fail("trades")
        rows = [
            t
            for t in self.trades
            if (start is None or t.executed_at >= start) and (end is None or t.executed_at <= end)
        ]
        rows.sort(key=lambda t: t.executed_at, reverse=True)
        return rows[:limit] if limit else rows

    def get_funding_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingCandle]:
        return []
