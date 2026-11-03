"""Bitfinex implementation of :class:`FundingMarketDataAdapter` (public endpoints only)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from fundpilot.config import Settings
from fundpilot.core.models import (
    FundingBookSnapshot,
    FundingCandle,
    FundingStat,
    FundingTicker,
    FundingTrade,
)
from fundpilot.core.time import to_ms, utc_now
from fundpilot.exchanges.bitfinex import parsers
from fundpilot.exchanges.bitfinex.client import BitfinexRestClient
from fundpilot.exchanges.bitfinex.symbols import funding_candle_key, validate_funding_symbol
from fundpilot.exchanges.ratelimit import TokenBucket

AGGREGATED_PRECISIONS = frozenset({"P0", "P1", "P2", "P3", "P4"})
BOOK_LENGTHS = frozenset({1, 25, 100})  # per official SDK type hints


def _ms(value: datetime | None) -> int | None:
    return None if value is None else to_ms(value)


class BitfinexPublicAdapter:
    exchange = "bitfinex"

    def __init__(
        self,
        client: BitfinexRestClient,
        *,
        funding_stats_frr_scale: Decimal = Decimal(365),
        candle_period_spec: str = "a30:p2:p30",
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._client = client
        self._frr_scale = funding_stats_frr_scale
        self._candle_period_spec = candle_period_spec
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> BitfinexPublicAdapter:
        client = BitfinexRestClient(
            settings.bitfinex_public_rest_url,
            timeout=settings.bitfinex_timeout_seconds,
            max_retries=settings.bitfinex_max_retries,
            rate_limiter=TokenBucket(settings.bitfinex_requests_per_minute),
            rate_limit_cooldown=settings.bitfinex_rate_limit_cooldown_seconds,
        )
        return cls(
            client,
            funding_stats_frr_scale=settings.bitfinex_funding_stats_frr_scale,
            candle_period_spec=settings.bitfinex_candle_period_spec,
        )

    def close(self) -> None:
        self._client.close()

    def is_operational(self) -> bool:
        return parsers.parse_platform_status(self._client.get("platform/status"))

    def get_last_price(self, pair: str) -> Decimal:
        """Last traded price of a trading pair, e.g. ``tUSTUSD`` (pair code unverified)."""
        if not pair.startswith("t") or len(pair) < 4:
            raise ValueError(f"not a Bitfinex trading pair: {pair!r}")
        return parsers.parse_trading_last_price(self._client.get(f"ticker/{pair}"))

    def get_funding_book(
        self, symbol: str, precision: str = "P0", length: int = 100
    ) -> FundingBookSnapshot:
        validate_funding_symbol(symbol)
        if precision not in AGGREGATED_PRECISIONS:
            raise ValueError(f"aggregated precision must be one of {sorted(AGGREGATED_PRECISIONS)}")
        if length not in BOOK_LENGTHS:
            raise ValueError(f"book length must be one of {sorted(BOOK_LENGTHS)}")
        payload = self._client.get(f"book/{symbol}/{precision}", {"len": length})
        return parsers.parse_funding_book(
            payload, symbol=symbol, precision=precision, captured_at=self._clock()
        )

    def get_funding_raw_book(self, symbol: str, length: int = 100) -> FundingBookSnapshot:
        validate_funding_symbol(symbol)
        if length not in BOOK_LENGTHS:
            raise ValueError(f"book length must be one of {sorted(BOOK_LENGTHS)}")
        payload = self._client.get(f"book/{symbol}/R0", {"len": length})
        return parsers.parse_funding_raw_book(payload, symbol=symbol, captured_at=self._clock())

    def get_funding_ticker(self, symbol: str) -> FundingTicker:
        validate_funding_symbol(symbol)
        payload = self._client.get(f"ticker/{symbol}")
        return parsers.parse_funding_ticker(payload, symbol=symbol, observed_at=self._clock())

    def get_funding_stats(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingStat]:
        validate_funding_symbol(symbol)
        payload = self._client.get(
            f"funding/stats/{symbol}/hist",
            {"start": _ms(start), "end": _ms(end), "limit": limit},
        )
        return parsers.parse_funding_stats(payload, symbol=symbol, frr_scale=self._frr_scale)

    def get_funding_trades(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingTrade]:
        validate_funding_symbol(symbol)
        payload = self._client.get(
            f"trades/{symbol}/hist",
            {"start": _ms(start), "end": _ms(end), "limit": limit, "sort": -1},
        )
        return parsers.parse_funding_trades(payload, symbol=symbol)

    def get_funding_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FundingCandle]:
        key = funding_candle_key(timeframe, symbol, self._candle_period_spec)
        payload = self._client.get(
            f"candles/{key}/hist",
            {"start": _ms(start), "end": _ms(end), "limit": limit, "sort": -1},
        )
        return parsers.parse_candles(payload, symbol=symbol, candle_key=key, timeframe=timeframe)
