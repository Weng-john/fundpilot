"""Observation and action encoding shared by the environment and :class:`PolicyStrategy`."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from numpy.typing import NDArray

from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.ml.features import FeatureBuilder
from fundpilot.strategies.base import Decision, MarketState, OfferRequest

ObsArray = NDArray[np.float32]

_MARKET_FEATURES = (
    "frr_logratio_mean_24h",
    "frr_z_24h",
    "frr_logchange_1h",
    "spread_rel",
    "book_imbalance",
    "trade_vwap_over_frr",
    "trade_long_share",
    "hour_sin",
    "hour_cos",
)
_IDX = {n: FeatureBuilder.names().index(n) for n in ("frr", *_MARKET_FEATURES)}

OBS_NAMES = (
    "frr_x1000",
    *_MARKET_FEATURES,
    "available_share",
    "offered_share",
    "lent_share",
    "lock_days_over_120",
    "lent_share_2d",
    "lent_share_7_30d",
    "lent_share_long",
    "pending_x1000",
)


def _clean(x: float, clip: float = 10.0) -> float:
    if math.isnan(x) or math.isinf(x):
        return 0.0
    return max(-clip, min(clip, x))


def observe(features: Sequence[float], portfolio: PortfolioState) -> ObsArray:
    total = float(portfolio.total) or 1.0
    frr = features[_IDX["frr"]]
    market = [_clean(frr * 1000)]
    for name in _MARKET_FEATURES:
        v = features[_IDX[name]]
        if name == "trade_vwap_over_frr":
            v = v - 1
        market.append(_clean(v))
    lent = float(portfolio.lent)
    lock = sum(
        float(c.amount) * c.remaining(portfolio.as_of).total_seconds() / 86_400
        for c in portfolio.credits
    )

    def share(lo: int, hi: int) -> float:
        return sum(float(c.amount) for c in portfolio.credits if lo <= c.period_days <= hi) / total

    port = [
        float(portfolio.available) / total,
        float(portfolio.offered) / total,
        lent / total,
        (lock / lent / 120) if lent else 0.0,
        share(2, 6),
        share(7, 30),
        share(31, 120),
        _clean(float(portfolio.pending_interest) / total * 1000),
    ]
    return np.array(market + port, dtype=np.float32)


@dataclass(frozen=True, slots=True)
class ActionGrid:
    premiums: tuple[float, ...] = (-0.05, 0.0, 0.05, 0.1, 0.2)
    periods: tuple[int, ...] = (2, 7, 30, 120)
    fractions: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0)

    @property
    def nvec(self) -> list[int]:
        return [len(self.premiums), len(self.periods), len(self.fractions), 2]

    def decode(
        self,
        action: Sequence[int] | NDArray[np.int64],
        market: MarketState,
        portfolio: PortfolioState,
    ) -> Decision:
        """``[premium, period, fraction of available, cancel-unfilled flag]`` -> Decision."""
        p_i, d_i, f_i, cancel = (int(a) for a in action)
        cancels = tuple(o.offer_id for o in portfolio.offers) if cancel else ()
        free = portfolio.available + (portfolio.offered if cancel else Decimal(0))
        amount = free * Decimal(str(self.fractions[f_i]))
        if market.frr is None or market.frr <= 0 or amount <= 0:
            return Decision(cancel=cancels)
        rate = market.frr * Decimal(str(1 + self.premiums[p_i]))
        return Decision(
            (OfferRequest(RateType.FIXED, rate, self.periods[d_i], amount, "rl"),), cancels
        )
