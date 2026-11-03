"""Pure parsers from Bitfinex REST v2 array payloads to normalised models.

Field layouts follow the official ``bitfinex-api-py`` 4.0.0 SDK serializers
(``bfxapi/types/serializers.py``). ``docs.bitfinex.com`` could not be reached
when this was written; see ``docs/bitfinex-integration.md`` for verification status.

Unit assumptions (UNVERIFIED against live responses, documented in the guide):

* book / ticker / trade / candle rates are daily decimal fractions;
* funding-stats FRR is reported as FRR/365 and is scaled by ``frr_scale``;
* funding book: ``AMOUNT > 0`` is a lender offer (ask), ``AMOUNT < 0`` a borrower bid.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from fundpilot.core.models import (
    BookSide,
    FundingBookLevel,
    FundingBookSnapshot,
    FundingCandle,
    FundingStat,
    FundingTicker,
    FundingTrade,
)
from fundpilot.core.time import from_ms
from fundpilot.exchanges.errors import ResponseParseError

EXCHANGE = "bitfinex"

FUNDING_TICKER_LEN = 16
FUNDING_STATS_LEN = 12
FUNDING_BOOK_LEN = 4
FUNDING_TRADE_LEN = 5
CANDLE_LEN = 6


def _row(value: Any, min_len: int, what: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ResponseParseError(f"{what}: expected list, got {type(value).__name__}")
    if len(value) < min_len:
        raise ResponseParseError(f"{what}: expected >= {min_len} fields, got {len(value)}")
    return value


def _rows(value: Any, what: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ResponseParseError(f"{what}: expected list of rows, got {type(value).__name__}")
    return value


def _dec(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ResponseParseError(f"{field}: expected number, got {value!r}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int | str):
        try:
            result = Decimal(value)
        except ArithmeticError as exc:
            raise ResponseParseError(f"{field}: invalid number {value!r}") from exc
    elif isinstance(value, float):  # only if caller bypassed Decimal parsing
        result = Decimal(repr(value))
    else:
        raise ResponseParseError(f"{field}: expected number, got {type(value).__name__}")
    if not result.is_finite():
        raise ResponseParseError(f"{field}: non-finite {value!r}")
    return result


def _opt_dec(value: Any, field: str) -> Decimal | None:
    return None if value is None else _dec(value, field)


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ResponseParseError(f"{field}: expected integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    raise ResponseParseError(f"{field}: expected integer, got {value!r}")


def _opt_int(value: Any, field: str) -> int | None:
    return None if value is None else _int(value, field)


def _ts(value: Any, field: str) -> datetime:
    return from_ms(_int(value, field))


def _side_from_amount(amount: Decimal) -> BookSide:
    return BookSide.BID if amount < 0 else BookSide.ASK


def parse_funding_book(
    payload: Any, *, symbol: str, precision: str, captured_at: datetime
) -> FundingBookSnapshot:
    """Aggregated book (P0-P4): rows of ``[RATE, PERIOD, COUNT, AMOUNT]``."""
    levels = []
    for i, raw in enumerate(_rows(payload, "funding book")):
        row = _row(raw, FUNDING_BOOK_LEN, f"funding book row {i}")
        amount = _dec(row[3], "amount")
        levels.append(
            FundingBookLevel(
                side=_side_from_amount(amount),
                rate=_dec(row[0], "rate"),
                period_days=_int(row[1], "period"),
                count=_int(row[2], "count"),
                amount=abs(amount),
            )
        )
    return FundingBookSnapshot(
        exchange=EXCHANGE,
        symbol=symbol,
        precision=precision,
        captured_at=captured_at,
        levels=tuple(levels),
    )


def parse_funding_raw_book(
    payload: Any, *, symbol: str, captured_at: datetime
) -> FundingBookSnapshot:
    """Raw book (R0): rows of ``[OFFER_ID, PERIOD, RATE, AMOUNT]``."""
    levels = []
    for i, raw in enumerate(_rows(payload, "funding raw book")):
        row = _row(raw, FUNDING_BOOK_LEN, f"funding raw book row {i}")
        amount = _dec(row[3], "amount")
        levels.append(
            FundingBookLevel(
                side=_side_from_amount(amount),
                offer_id=_int(row[0], "offer_id"),
                period_days=_int(row[1], "period"),
                rate=_dec(row[2], "rate"),
                amount=abs(amount),
            )
        )
    return FundingBookSnapshot(
        exchange=EXCHANGE,
        symbol=symbol,
        precision="R0",
        captured_at=captured_at,
        levels=tuple(levels),
    )


def parse_funding_ticker(payload: Any, *, symbol: str, observed_at: datetime) -> FundingTicker:
    """Single funding ticker (``ticker/{symbol}``), 16 fields; indices 13-14 are placeholders."""
    row = _row(payload, FUNDING_TICKER_LEN, "funding ticker")
    return FundingTicker(
        exchange=EXCHANGE,
        symbol=symbol,
        observed_at=observed_at,
        frr=_opt_dec(row[0], "frr"),
        bid=_opt_dec(row[1], "bid"),
        bid_period_days=_opt_int(row[2], "bid_period"),
        bid_size=_opt_dec(row[3], "bid_size"),
        ask=_opt_dec(row[4], "ask"),
        ask_period_days=_opt_int(row[5], "ask_period"),
        ask_size=_opt_dec(row[6], "ask_size"),
        daily_change=_opt_dec(row[7], "daily_change"),
        daily_change_relative=_opt_dec(row[8], "daily_change_relative"),
        last_price=_opt_dec(row[9], "last_price"),
        volume=_opt_dec(row[10], "volume"),
        high=_opt_dec(row[11], "high"),
        low=_opt_dec(row[12], "low"),
        frr_amount_available=_opt_dec(row[15], "frr_amount_available"),
    )


def parse_funding_stats(payload: Any, *, symbol: str, frr_scale: Decimal) -> list[FundingStat]:
    """``funding/stats/{symbol}/hist`` rows (12 fields; several placeholders)."""
    stats = []
    for i, raw in enumerate(_rows(payload, "funding stats")):
        row = _row(raw, FUNDING_STATS_LEN, f"funding stats row {i}")
        frr_raw = _opt_dec(row[3], "frr")
        stats.append(
            FundingStat(
                exchange=EXCHANGE,
                symbol=symbol,
                observed_at=_ts(row[0], "mts"),
                frr_raw=frr_raw,
                frr=None if frr_raw is None else frr_raw * frr_scale,
                avg_period_days=_opt_dec(row[4], "avg_period"),
                funding_amount=_opt_dec(row[7], "funding_amount"),
                funding_amount_used=_opt_dec(row[8], "funding_amount_used"),
                funding_below_threshold=_opt_dec(row[11], "funding_below_threshold"),
            )
        )
    return stats


def parse_funding_trades(payload: Any, *, symbol: str) -> list[FundingTrade]:
    """``trades/{symbol}/hist`` funding rows: ``[ID, MTS, AMOUNT, RATE, PERIOD]``."""
    trades = []
    for i, raw in enumerate(_rows(payload, "funding trades")):
        row = _row(raw, FUNDING_TRADE_LEN, f"funding trade row {i}")
        trades.append(
            FundingTrade(
                exchange=EXCHANGE,
                symbol=symbol,
                trade_id=_int(row[0], "id"),
                executed_at=_ts(row[1], "mts"),
                amount=_dec(row[2], "amount"),
                rate=_dec(row[3], "rate"),
                period_days=_int(row[4], "period"),
            )
        )
    return trades


def parse_candles(
    payload: Any, *, symbol: str, candle_key: str, timeframe: str
) -> list[FundingCandle]:
    """Candle rows: ``[MTS, OPEN, CLOSE, HIGH, LOW, VOLUME]`` (note OPEN/CLOSE order)."""
    candles = []
    for i, raw in enumerate(_rows(payload, "candles")):
        row = _row(raw, CANDLE_LEN, f"candle row {i}")
        candles.append(
            FundingCandle(
                exchange=EXCHANGE,
                symbol=symbol,
                candle_key=candle_key,
                timeframe=timeframe,
                open_time=_ts(row[0], "mts"),
                open=_dec(row[1], "open"),
                close=_dec(row[2], "close"),
                high=_dec(row[3], "high"),
                low=_dec(row[4], "low"),
                volume=_dec(row[5], "volume"),
            )
        )
    return candles


def parse_platform_status(payload: Any) -> bool:
    """``platform/status`` returns ``[1]`` when operative, ``[0]`` in maintenance."""
    row = _row(payload, 1, "platform status")
    return _int(row[0], "status") == 1


TRADING_TICKER_LEN = 10


def parse_trading_last_price(payload: Any) -> Decimal:
    """Trading ticker ``ticker/t{PAIR}``: 10 fields, ``LAST_PRICE`` at index 6 (per SDK)."""
    row = _row(payload, TRADING_TICKER_LEN, "trading ticker")
    return _dec(row[6], "last_price")
