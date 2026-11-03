"""Point-in-time feature engineering.

:class:`FeatureBuilder` consumes market states in chronological order and emits a feature
vector for each one using only that state and earlier ones (rolling windows are kept
internally). Missing inputs are ``nan``.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from fundpilot.core.models import BookSide
from fundpilot.strategies.base import MarketState

NAN = float("nan")
# Time-based windows so features mean the same thing at any sampling cadence.
WINDOWS: tuple[tuple[str, timedelta], ...] = (
    ("1h", timedelta(hours=1)),
    ("6h", timedelta(hours=6)),
    ("24h", timedelta(hours=24)),
)


def _f(x: Decimal | None) -> float:
    return NAN if x is None else float(x)


def _mean_std(values: list[float]) -> tuple[float, float]:
    mean = sum(values) / len(values)
    std = math.sqrt(sum((x - mean) ** 2 for x in values) / (len(values) - 1))
    return mean, std


@dataclass(slots=True)
class FeatureBuilder:
    trade_window: timedelta = timedelta(hours=1)
    depth_band: float = 0.10  # relative band around best price for depth features
    _history: deque[tuple[datetime, float]] = field(default_factory=deque)

    @staticmethod
    def names() -> list[str]:
        names = ["frr", "log_frr"]
        for label, _ in WINDOWS:
            names += [f"frr_mean_{label}", f"frr_std_{label}", f"frr_logratio_mean_{label}"]
        names += [
            "frr_z_24h",
            "frr_logchange_1h",
            "best_ask",
            "best_bid",
            "spread_rel",
            "ask_depth_band",
            "bid_depth_band",
            "book_imbalance",
            "ask_depth_total",
            "bid_depth_total",
            "ask_levels",
            "trade_count",
            "trade_volume",
            "trade_vwap",
            "trade_vwap_over_frr",
            "trade_long_share",
            "hour_sin",
            "hour_cos",
            "weekday",
        ]
        return names

    def update(self, m: MarketState) -> list[float]:
        """Features at ``m.as_of`` from ``m`` and earlier states only."""
        now = m.as_of
        frr = _f(m.frr) if m.frr is not None and m.frr > 0 else NAN
        have = not math.isnan(frr)
        horizon = max(w for _, w in WINDOWS)
        while self._history and self._history[0][0] < now - horizon:
            self._history.popleft()
        out = [frr, math.log(frr) if have else NAN]
        z = NAN
        for label, width in WINDOWS:
            window = [v for t, v in self._history if t >= now - width]
            if len(window) >= 2:
                mean, std = _mean_std(window)
                out += [mean, std, math.log(frr / mean) if have and mean > 0 else NAN]
                if label == "24h" and have:
                    z = (frr - mean) / std if std > 0 else 0.0
            else:
                out += [NAN, NAN, NAN]
        out.append(z)
        past = next((v for t, v in self._history if t >= now - timedelta(hours=1)), None)
        out.append(
            math.log(frr / past)
            if have
            and past
            and past > 0
            and self._history
            and self._history[0][0] <= now - timedelta(minutes=50)
            else NAN
        )
        out += self._book(m)
        out += self._trades(m, frr)
        hour = now.hour + now.minute / 60
        out += [
            math.sin(2 * math.pi * hour / 24),
            math.cos(2 * math.pi * hour / 24),
            float(now.weekday()),
        ]
        if have:
            self._history.append((now, frr))
        return out

    def _book(self, m: MarketState) -> list[float]:
        asks = m.levels(BookSide.ASK)
        bids = m.levels(BookSide.BID)
        best_ask = min((float(lv.rate) for lv in asks), default=NAN)
        best_bid = max((float(lv.rate) for lv in bids), default=NAN)
        spread = (best_ask - best_bid) / best_ask if asks and bids and best_ask > 0 else NAN
        ask_band = sum(
            float(lv.amount)
            for lv in asks
            if asks and float(lv.rate) <= best_ask * (1 + self.depth_band)
        )
        bid_band = sum(
            float(lv.amount)
            for lv in bids
            if bids and float(lv.rate) >= best_bid * (1 - self.depth_band)
        )
        total = ask_band + bid_band
        return [
            best_ask,
            best_bid,
            spread,
            ask_band if asks else NAN,
            bid_band if bids else NAN,
            (bid_band - ask_band) / total if total > 0 else NAN,
            sum(float(lv.amount) for lv in asks) if asks else NAN,
            sum(float(lv.amount) for lv in bids) if bids else NAN,
            float(len(asks)),
        ]

    def _trades(self, m: MarketState, frr: float) -> list[float]:
        start = m.as_of - self.trade_window
        recent = [t for t in m.trades if start < t.executed_at <= m.as_of]
        vol = sum(abs(float(t.amount)) for t in recent)
        vwap = sum(abs(float(t.amount)) * float(t.rate) for t in recent) / vol if vol else NAN
        long_vol = sum(abs(float(t.amount)) for t in recent if t.period_days >= 30)
        return [
            float(len(recent)),
            vol,
            vwap,
            vwap / frr if vol and not math.isnan(frr) and frr > 0 else NAN,
            long_vol / vol if vol else NAN,
        ]


def build_features(states: Iterable[MarketState]) -> tuple[list[MarketState], list[list[float]]]:
    """Features for every non-stale state, in order (stale states still update nothing)."""
    builder = FeatureBuilder()
    kept: list[MarketState] = []
    rows: list[list[float]] = []
    for s in states:
        if s.stale:
            continue
        rows.append(builder.update(s))
        kept.append(s)
    return kept, rows
