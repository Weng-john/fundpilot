"""Adapter contracts.

Strategies, collectors and the backtester depend only on these protocols, never
on a concrete exchange client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from fundpilot.core.models import (
    FundingBookSnapshot,
    FundingCandle,
    FundingStat,
    FundingTicker,
    FundingTrade,
)


@runtime_checkable
class FundingMarketDataAdapter(Protocol):
    """Read-only, unauthenticated funding market data."""

    @property
    def exchange(self) -> str: ...

    def is_operational(self) -> bool: ...

    def get_funding_book(
        self, symbol: str, precision: str = "P0", length: int = 100
    ) -> FundingBookSnapshot: ...

    def get_funding_raw_book(self, symbol: str, length: int = 100) -> FundingBookSnapshot: ...

    def get_funding_ticker(self, symbol: str) -> FundingTicker: ...

    def get_funding_stats(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingStat]: ...

    def get_funding_trades(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingTrade]: ...

    def get_funding_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingCandle]: ...
