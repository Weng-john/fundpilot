"""Live market-state source and health monitoring for paper trading."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from fundpilot.core.models import FundingBookSnapshot, FundingTicker, FundingTrade
from fundpilot.core.time import utc_now
from fundpilot.exchanges.base import FundingMarketDataAdapter
from fundpilot.execution.controls import Controls
from fundpilot.risk.manager import HealthStatus
from fundpilot.strategies.base import MarketState

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LiveMarketSource:
    """Polls the adapter and assembles a point-in-time :class:`MarketState`.

    Trades are accumulated in a rolling buffer (deduplicated by id) so that fills can be
    simulated from every trade seen since the previous step. On failure the last known data
    is returned with ``stale=True`` and the failure counter increases.
    """

    adapter: FundingMarketDataAdapter
    symbol: str
    book_precision: str = "P0"
    book_length: int = 100
    trades_limit: int = 250
    trade_lookback: timedelta = timedelta(hours=24)
    max_staleness: timedelta = timedelta(minutes=5)
    clock: Callable[[], datetime] = utc_now
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    _ticker: FundingTicker | None = None
    _book: FundingBookSnapshot | None = None
    _trades: dict[int, FundingTrade] = field(default_factory=dict)

    def __call__(self) -> MarketState:
        now = self.clock()
        try:
            ticker = self.adapter.get_funding_ticker(self.symbol)
            book = self.adapter.get_funding_book(self.symbol, self.book_precision, self.book_length)
            trades = self.adapter.get_funding_trades(self.symbol, limit=self.trades_limit)
        except Exception as exc:  # any adapter failure -> stale state, counted
            self.consecutive_failures += 1
            logger.warning(
                "market data fetch failed (%d in a row): %s", self.consecutive_failures, exc
            )
            return self._state(now, stale=True)
        self.consecutive_failures = 0
        self.last_success_at = now
        self._ticker, self._book = ticker, book
        for t in trades:
            if t.executed_at <= now:  # never admit timestamps from the future
                self._trades[t.trade_id] = t
        cutoff = now - self.trade_lookback
        self._trades = {k: v for k, v in self._trades.items() if v.executed_at > cutoff}
        return self._state(now, stale=False)

    def _state(self, now: datetime, *, stale: bool) -> MarketState:
        if self.last_success_at is None or now - self.last_success_at > self.max_staleness:
            stale = True
        trades = tuple(sorted(self._trades.values(), key=lambda t: t.executed_at))
        return MarketState(now, self.symbol, self._ticker, self._book, trades, stale)


@dataclass(slots=True)
class HealthMonitor:
    controls: Controls
    source: LiveMarketSource
    platform_check: Callable[[], bool] | None = None
    stablecoin_price: Callable[[], Decimal] | None = None
    platform_check_interval: timedelta = timedelta(minutes=5)
    _platform: bool | None = None
    _platform_checked_at: datetime | None = None
    _price: Decimal | None = None

    def status(self, now: datetime) -> HealthStatus:
        if self.platform_check is not None and (
            self._platform_checked_at is None
            or now - self._platform_checked_at >= self.platform_check_interval
        ):
            try:
                self._platform = self.platform_check()
            except Exception as exc:  # unknown status is not "operational"
                logger.warning("platform status check failed: %s", exc)
                self._platform = None
            self._platform_checked_at = now
        if self.stablecoin_price is not None:
            try:
                self._price = self.stablecoin_price()
            except Exception as exc:
                logger.warning("stablecoin price check failed: %s", exc)
        stop, reason = self.controls.emergency_state()
        return HealthStatus(
            as_of=now,
            platform_operational=self._platform,
            consecutive_api_failures=self.source.consecutive_failures,
            last_market_data_at=self.source.last_success_at,
            stablecoin_price=self._price,
            emergency_stop=stop,
            emergency_reason=reason,
        )
