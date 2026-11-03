"""Strategy that picks the rate maximising ``rate x P(fill within window)``.

The fill model is loaded from the registry together with its training window, which the
experiment runner uses to refuse backtests that overlap the training data (leakage).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.ml import registry
from fundpilot.ml.features import FeatureBuilder
from fundpilot.ml.models import LightGBMFillModel
from fundpilot.strategies.base import Decision, MarketState, OfferRequest


@dataclass(slots=True)
class FillAwareRateStrategy:
    model_dir: Path
    period_days: int = 2
    premiums: tuple[float, ...] = (-0.05, 0.0, 0.05, 0.1, 0.2, 0.3)
    min_probability: float = 0.2
    reprice_after: timedelta = timedelta(minutes=30)
    reprice_tolerance: Decimal = Decimal("0.02")
    min_amount: Decimal = Decimal(150)
    _model: LightGBMFillModel = field(init=False, repr=False)
    _features: FeatureBuilder = field(default_factory=FeatureBuilder, init=False, repr=False)
    training_start: datetime = field(init=False)
    training_end: datetime = field(init=False)
    label_source: str | None = field(init=False)

    def __post_init__(self) -> None:
        meta = registry.load_metadata(self.model_dir)
        if meta.get("task") != "fill_probability":
            raise ValueError(f"{self.model_dir} is not a fill-probability model")
        self.training_start, last_sample = registry.training_window(meta)
        # labels look ahead by the horizon, so training data extends to sample + horizon
        self.training_end = last_sample + timedelta(seconds=float(meta["label_horizon_seconds"]))
        self.label_source = meta.get("label_source")
        self._model = LightGBMFillModel.load(self.model_dir)

    @property
    def name(self) -> str:
        return f"fill_aware({self.period_days}d,{self.model_dir.name})"

    def choose(
        self, market: MarketState, amount: Decimal, feats: list[float]
    ) -> tuple[Decimal, float] | None:
        if market.frr is None or market.frr <= 0:
            return None
        log_amount = math.log(max(float(amount), 1.0))
        X = np.array(
            [[*feats, p, float(self.period_days), log_amount] for p in self.premiums],
            dtype=np.float64,
        )
        probs = self._model.predict_proba(X)
        best: tuple[Decimal, float, float] | None = None
        for prem, prob in zip(self.premiums, probs, strict=True):
            if prob < self.min_probability:
                continue
            rate = market.frr * Decimal(str(1 + prem))
            value = float(rate) * float(prob)
            if best is None or value > best[2]:
                best = (rate, float(prob), value)
        return None if best is None else (best[0], best[1])

    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision:
        feats = self._features.update(market)
        choice = self.choose(market, max(portfolio.available, self.min_amount), feats)
        if choice is None:
            return Decision(notes=("no rate meets the minimum fill probability",))
        rate, prob = choice
        cancels = tuple(
            o.offer_id
            for o in portfolio.offers
            if o.rate is not None
            and o.age(market.as_of) >= self.reprice_after
            and abs(o.rate - rate) > rate * self.reprice_tolerance
        )
        idle = portfolio.available + sum(
            (o.amount for o in portfolio.offers if o.offer_id in cancels), Decimal(0)
        )
        places: tuple[OfferRequest, ...] = ()
        if idle >= self.min_amount:
            places = (OfferRequest(RateType.FIXED, rate, self.period_days, idle, "fill_aware"),)
        return Decision(places, cancels, (f"rate={rate}", f"p_fill={prob:.3f}"))
