"""Adaptive Funding Ladder.

Spreads capital over duration buckets (short / medium / long). When the reference
rate is high relative to its own recent history the ladder tilts towards long
durations (locking in high rates); when it is low it tilts towards short durations
(keeping capital liquid for a rebound). Unfilled offers are repriced after a delay.

Only information observed at or before ``market.as_of`` is used: the rolling
history is built from successive calls.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.strategies.base import Decision, MarketState, OfferRequest


@dataclass(frozen=True, slots=True)
class LadderBucket:
    name: str
    period_days: int
    base_weight: Decimal
    duration_sign: int  # -1 short (favoured when rates low), 0 neutral, +1 long


def _default_buckets() -> tuple[LadderBucket, ...]:
    return (
        LadderBucket("short", 2, Decimal("0.5"), -1),
        LadderBucket("medium", 30, Decimal("0.3"), 0),
        LadderBucket("long", 120, Decimal("0.2"), 1),
    )


def _quantile(sorted_values: list[Decimal], q: Decimal) -> Decimal:
    idx = int((len(sorted_values) - 1) * q)
    return sorted_values[max(0, min(idx, len(sorted_values) - 1))]


@dataclass(slots=True)
class AdaptiveFundingLadder:
    buckets: tuple[LadderBucket, ...] = field(default_factory=_default_buckets)
    tilt: Decimal = Decimal("0.8")  # 0 = static weights
    reserve_fraction: Decimal = Decimal(0)
    history_size: int = 1440
    min_history: int = 30
    rate_quantile: Decimal = Decimal("0.5")  # quantile of recent trade rates used for pricing
    frr_multiplier: Decimal = Decimal(1)  # fallback pricing when no comparable trades exist
    min_rate: Decimal = Decimal("0.00001")
    reprice_after: timedelta = timedelta(hours=1)
    reprice_tolerance: Decimal = Decimal("0.05")  # relative rate change that justifies replace
    min_amount: Decimal = Decimal(150)
    _history: deque[Decimal] = field(default_factory=deque, init=False, repr=False)
    _sorted: list[Decimal] = field(default_factory=list, init=False, repr=False)

    @property
    def name(self) -> str:
        return "adaptive_ladder(" + ",".join(f"{b.period_days}d" for b in self.buckets) + ")"

    # -- rolling statistics -------------------------------------------------
    def _observe(self, value: Decimal) -> None:
        self._history.append(value)
        insort(self._sorted, value)
        if len(self._history) > self.history_size:
            old = self._history.popleft()
            del self._sorted[bisect_left(self._sorted, old)]

    def percentile(self, value: Decimal) -> Decimal | None:
        if len(self._sorted) < self.min_history:
            return None
        return Decimal(bisect_left(self._sorted, value)) / Decimal(len(self._sorted))

    @staticmethod
    def reference_rate(market: MarketState) -> Decimal | None:
        if market.frr is not None and market.frr > 0:
            return market.frr
        rates = sorted(t.rate for t in market.trades)
        return rates[len(rates) // 2] if rates else None

    # -- weights and pricing ------------------------------------------------
    def weights(self, pct: Decimal | None) -> dict[str, Decimal]:
        signal = Decimal(0) if pct is None else (pct * 2 - 1)  # -1 .. +1
        raw = {
            b.name: max(Decimal(0), b.base_weight * (1 + self.tilt * signal * b.duration_sign))
            for b in self.buckets
        }
        total = sum(raw.values(), Decimal(0))
        if total == 0:
            return {b.name: Decimal(1) / len(self.buckets) for b in self.buckets}
        return {k: v / total for k, v in raw.items()}

    def price(self, bucket: LadderBucket, market: MarketState) -> Decimal | None:
        lo, hi = self._bucket_range(bucket)
        rates = sorted(t.rate for t in market.trades if lo <= t.period_days <= hi)
        if rates:
            rate = _quantile(rates, self.rate_quantile)
        elif market.frr is not None and market.frr > 0:
            rate = market.frr * self.frr_multiplier
        else:
            return None
        return max(rate, self.min_rate)

    def _bucket_range(self, bucket: LadderBucket) -> tuple[int, int]:
        periods = sorted(b.period_days for b in self.buckets)
        i = periods.index(bucket.period_days)
        lo = 2 if i == 0 else (periods[i - 1] + bucket.period_days) // 2 + 1
        hi = 120 if i == len(periods) - 1 else (bucket.period_days + periods[i + 1]) // 2
        return lo, hi

    # -- decision -----------------------------------------------------------
    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision:
        ref = self.reference_rate(market)
        pct = None
        if ref is not None:
            pct = self.percentile(ref)
            self._observe(ref)
        weights = self.weights(pct)
        prices = {b.name: self.price(b, market) for b in self.buckets}

        cancels: list[str] = []
        released = Decimal(0)
        for offer in portfolio.offers:
            bucket = next((b for b in self.buckets if b.period_days == offer.period_days), None)
            target = prices.get(bucket.name) if bucket else None
            if (
                bucket is not None
                and target is not None
                and offer.rate is not None
                and offer.age(market.as_of) >= self.reprice_after
                and abs(offer.rate - target) > target * self.reprice_tolerance
            ):
                cancels.append(offer.offer_id)
                released += offer.amount

        investable = portfolio.total * (1 - self.reserve_fraction)
        free = portfolio.available + released - portfolio.total * self.reserve_fraction
        cancelled = set(cancels)
        deficits: dict[str, Decimal] = {}
        for b in self.buckets:
            current = sum(
                (
                    o.amount
                    for o in portfolio.offers
                    if o.period_days == b.period_days and o.offer_id not in cancelled
                ),
                Decimal(0),
            ) + sum(
                (c.amount for c in portfolio.credits if c.period_days == b.period_days),
                Decimal(0),
            )
            deficits[b.name] = max(Decimal(0), weights[b.name] * investable - current)

        total_deficit = sum(deficits.values(), Decimal(0))
        places: list[OfferRequest] = []
        if free >= self.min_amount and total_deficit > 0:
            budget = min(free, total_deficit)
            for b in self.buckets:
                rate = prices[b.name]
                amount = budget * deficits[b.name] / total_deficit
                if rate is None or amount < self.min_amount:
                    continue
                places.append(OfferRequest(RateType.FIXED, rate, b.period_days, amount, b.name))

        notes = (
            f"ref_rate={ref}",
            f"percentile={pct}",
            "weights=" + ",".join(f"{k}:{v:.3f}" for k, v in weights.items()),
        )
        return Decision(tuple(places), tuple(cancels), notes)
