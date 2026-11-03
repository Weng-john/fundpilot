"""Market indicators computed point-in-time from completed candles."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise

from fundpilot.core.models import TradingCandle

TIMEFRAMES = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "1D": timedelta(days=1),
}


def realized_volatility(closes: Sequence[float], periods_per_year: float) -> float:
    """Annualised standard deviation of log returns (sample std); ``nan`` if < 3 closes."""
    if len(closes) < 3 or any(c <= 0 for c in closes):
        return float("nan")
    rets = [math.log(b / a) for a, b in pairwise(closes)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * periods_per_year)


class VolatilitySeries:
    """Rolling realised volatility, available only once each candle has *closed*."""

    def __init__(self, candles: Sequence[TradingCandle], window: int = 24) -> None:
        if not candles:
            self._times: list[datetime] = []
            self._values: list[float] = []
            return
        tf = TIMEFRAMES[candles[0].timeframe]
        per_year = timedelta(days=365) / tf
        ordered = sorted({c.open_time: c for c in candles}.values(), key=lambda c: c.open_time)
        closes = [float(c.close) for c in ordered]
        self._times = [c.open_time + tf for c in ordered]  # close time = availability time
        self._values = [
            realized_volatility(closes[max(0, i - window) : i + 1], per_year)
            for i in range(len(closes))
        ]

    def at(self, t: datetime) -> float:
        i = bisect_right(self._times, t)
        return self._values[i - 1] if i else float("nan")
