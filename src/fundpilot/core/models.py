"""Normalised, exchange-agnostic market data models.

Conventions:

* every ``rate`` field is a **daily decimal fraction** (see :mod:`fundpilot.core.rates`);
* every amount is a ``Decimal`` in units of the funding currency;
* every timestamp is an aware UTC ``datetime``;
* ``origin`` records whether a row is a genuine exchange observation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

from fundpilot.core.time import ensure_utc


class DataOrigin(StrEnum):
    """Provenance of a stored record. Only ``EXCHANGE_API`` rows are genuine observations."""

    EXCHANGE_API = "exchange_api"
    RECONSTRUCTED = "reconstructed"
    SYNTHETIC = "synthetic"


class BookSide(StrEnum):
    """Side of the funding book.

    ``BID``: borrowers asking for funding (demand).
    ``ASK``: lenders offering funding (supply).
    """

    BID = "bid"
    ASK = "ask"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)


class _Timestamped(_Model):
    @field_validator("*", mode="after")
    @classmethod
    def _utc(cls, value: object) -> object:
        if isinstance(value, datetime):
            return ensure_utc(value)
        return value


class FundingBookLevel(_Model):
    side: BookSide
    rate: Decimal
    period_days: int
    amount: Decimal  # absolute (non-negative) size
    count: int | None = None  # aggregated books only
    offer_id: int | None = None  # raw books only


class FundingBookSnapshot(_Timestamped):
    exchange: str
    symbol: str
    precision: str  # e.g. P0..P4 (aggregated) or R0 (raw)
    captured_at: datetime
    levels: tuple[FundingBookLevel, ...]
    origin: DataOrigin = DataOrigin.EXCHANGE_API

    def side(self, side: BookSide) -> tuple[FundingBookLevel, ...]:
        return tuple(level for level in self.levels if level.side is side)


class FundingTicker(_Timestamped):
    exchange: str
    symbol: str
    observed_at: datetime
    frr: Decimal | None
    bid: Decimal | None
    bid_period_days: int | None
    bid_size: Decimal | None
    ask: Decimal | None
    ask_period_days: int | None
    ask_size: Decimal | None
    daily_change: Decimal | None
    daily_change_relative: Decimal | None
    last_price: Decimal | None
    volume: Decimal | None
    high: Decimal | None
    low: Decimal | None
    frr_amount_available: Decimal | None
    origin: DataOrigin = DataOrigin.EXCHANGE_API


class FundingStat(_Timestamped):
    exchange: str
    symbol: str
    observed_at: datetime
    frr_raw: Decimal | None  # value exactly as reported by the exchange
    frr: Decimal | None  # normalised daily fraction
    avg_period_days: Decimal | None
    funding_amount: Decimal | None
    funding_amount_used: Decimal | None
    funding_below_threshold: Decimal | None
    origin: DataOrigin = DataOrigin.EXCHANGE_API


class FundingCandle(_Timestamped):
    """OHLC of funding *rates* (daily fractions) plus traded volume."""

    exchange: str
    symbol: str
    candle_key: str  # exchange-specific key, kept for reproducibility
    timeframe: str
    open_time: datetime
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    origin: DataOrigin = DataOrigin.EXCHANGE_API


class FundingTrade(_Timestamped):
    exchange: str
    symbol: str
    trade_id: int
    executed_at: datetime
    amount: Decimal  # signed, exactly as reported (sign semantics are exchange-specific)
    rate: Decimal
    period_days: int
    origin: DataOrigin = DataOrigin.EXCHANGE_API
