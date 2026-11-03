"""Bitfinex symbol helpers."""

from __future__ import annotations

import re

_FUNDING_RE = re.compile(r"^f[A-Z0-9]{3,10}$")
# Bitfinex candle timeframes as commonly documented (UNVERIFIED in this session).
CANDLE_TIMEFRAMES = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "1D", "1W", "14D", "1M"}
)


def validate_funding_symbol(symbol: str) -> str:
    """Funding symbols are ``f`` + currency code, e.g. ``fUSD``.

    NOTE: Bitfinex's code for Tether USDt is believed to be ``UST`` (``fUST``);
    this could not be verified here and is kept configurable.
    """
    if not _FUNDING_RE.match(symbol):
        raise ValueError(f"not a Bitfinex funding symbol: {symbol!r}")
    return symbol


def funding_candle_key(timeframe: str, symbol: str, period_spec: str) -> str:
    """Build a funding candle key, e.g. ``trade:1h:fUST:a30:p2:p30``."""
    if timeframe not in CANDLE_TIMEFRAMES:
        raise ValueError(f"unsupported candle timeframe: {timeframe!r}")
    validate_funding_symbol(symbol)
    if not re.fullmatch(r"[ap]\d+(:[ap]\d+)*", period_spec):
        raise ValueError(f"invalid funding candle period spec: {period_spec!r}")
    return f"trade:{timeframe}:{symbol}:{period_spec}"
