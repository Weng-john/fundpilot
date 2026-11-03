"""Training datasets for the two prediction tasks.

Label provenance is explicit and carried into every report:

* ``observed``  - fills of real exchange offers (live mode). The only ground truth.
* ``simulated`` - fills from paper trading; produced by a fill *model*, not the market.
* ``inferred``  - hypothetical offers labelled from public trades + book; an approximation.

Only ``observed`` labels may be described as real fill observations.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundpilot.core.models import BookSide, FundingTrade
from fundpilot.data.models import FundingOfferEventRow
from fundpilot.ml.features import FeatureBuilder
from fundpilot.ml.validation import FloatArray
from fundpilot.strategies.base import MarketState

OFFER_FEATURES = ["offer_premium", "offer_period", "offer_log_amount"]


class LabelSource(StrEnum):
    OBSERVED = "observed"
    SIMULATED = "simulated"
    INFERRED = "inferred"


LABEL_WARNINGS = {
    LabelSource.OBSERVED: "Labels are observed fills of real exchange offers.",
    LabelSource.SIMULATED: "SIMULATED LABELS - fills produced by a paper-trading fill model, "
    "not by the market. Not ground truth.",
    LabelSource.INFERRED: "INFERRED LABELS - hypothetical offers labelled from public trades "
    "and book depth. Not ground truth.",
}


@dataclass(frozen=True, slots=True)
class Dataset:
    times: list[datetime]
    X: FloatArray
    y: FloatArray
    feature_names: list[str]
    label_horizon: timedelta
    current_rate: FloatArray | None = None  # forecasting only
    label_source: LabelSource | None = None  # fill probability only

    def __len__(self) -> int:
        return len(self.times)

    def fingerprint(self) -> str:
        import hashlib  # noqa: PLC0415

        h = hashlib.sha256()
        h.update(np.ascontiguousarray(np.nan_to_num(self.X, nan=-9e99)).tobytes())
        h.update(np.ascontiguousarray(self.y).tobytes())
        h.update("|".join(t.isoformat() for t in self.times).encode())
        return h.hexdigest()


def _matrix(rows: list[list[float]], n_features: int) -> FloatArray:
    return np.array(rows, dtype=np.float64).reshape(len(rows), n_features)


def _features_by_time(states: Sequence[MarketState]) -> tuple[list[MarketState], list[list[float]]]:
    builder = FeatureBuilder()
    kept, rows = [], []
    for s in states:
        if s.stale:
            continue
        rows.append(builder.update(s))
        kept.append(s)
    return kept, rows


# ---------------------------------------------------------------------------
# Task 1: rate forecasting
# ---------------------------------------------------------------------------
def forecast_dataset(
    states: Sequence[MarketState],
    future_rate: Callable[[datetime], Decimal | None],
    horizon: timedelta,
    *,
    sample_every: timedelta = timedelta(hours=1),
    end: datetime | None = None,
) -> Dataset:
    """Target: ``log(FRR(t + horizon) / FRR(t))``; features use data up to ``t`` only.

    Labels must fall inside the data window: samples with ``t + horizon > end`` (default: the
    last state's time) are dropped.
    """
    kept, rows = _features_by_time(states)
    last = end or (states[-1].as_of if states else None)
    times, X, y, cur = [], [], [], []
    next_sample: datetime | None = None
    for s, row in zip(kept, rows, strict=True):
        if next_sample is not None and s.as_of < next_sample:
            continue
        next_sample = s.as_of + sample_every
        if last is None or s.as_of + horizon > last:
            break
        now = s.frr
        fut = future_rate(s.as_of + horizon)
        if now is None or now <= 0 or fut is None or fut <= 0:
            continue
        times.append(s.as_of)
        X.append(row)
        y.append(math.log(float(fut) / float(now)))
        cur.append(float(now))
    return Dataset(
        times,
        _matrix(X, len(FeatureBuilder.names())),
        np.array(y, dtype=np.float64),
        FeatureBuilder.names(),
        horizon,
        current_rate=np.array(cur, dtype=np.float64),
    )


# ---------------------------------------------------------------------------
# Task 2: fill probability
# ---------------------------------------------------------------------------
def inferred_fill_dataset(
    states: Sequence[MarketState],
    trades: Sequence[FundingTrade],
    *,
    window: timedelta = timedelta(hours=1),
    premiums: Sequence[float] = (-0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.3),
    periods: Sequence[int] = (2, 30),
    amount: float = 1000.0,
    sample_every: timedelta = timedelta(hours=1),
) -> Dataset:
    """Hypothetical offers at ``FRR * (1 + premium)``; label = public volume of funding *taken*
    (negative amount) at >= our rate and the same period within ``window`` exceeded the ask
    depth ahead of us plus our amount."""
    ordered = sorted(trades, key=lambda t: t.executed_at)
    ts = [t.executed_at for t in ordered]
    kept, rows = _features_by_time(states)
    times, X, y = [], [], []
    next_sample: datetime | None = None
    for s, row in zip(kept, rows, strict=True):
        if next_sample is not None and s.as_of < next_sample:
            continue
        next_sample = s.as_of + sample_every
        if s.frr is None or s.frr <= 0:
            continue
        lo, hi = bisect_right(ts, s.as_of), bisect_right(ts, s.as_of + window)
        future = ordered[lo:hi]
        for period in periods:
            for prem in premiums:
                rate = float(s.frr) * (1 + prem)
                ahead = sum(
                    float(lv.amount)
                    for lv in s.levels(BookSide.ASK)
                    if lv.period_days == period and float(lv.rate) < rate
                )
                volume = sum(
                    abs(float(t.amount))
                    for t in future
                    # only funding *taken* (negative amount) fills resting offers
                    if t.amount < 0 and t.period_days == period and float(t.rate) >= rate
                )
                times.append(s.as_of)
                X.append([*row, prem, float(period), math.log(amount)])
                y.append(1.0 if volume >= ahead + amount else 0.0)
    return Dataset(
        times,
        _matrix(X, len(FeatureBuilder.names()) + len(OFFER_FEATURES)),
        np.array(y, dtype=np.float64),
        FeatureBuilder.names() + OFFER_FEATURES,
        window,
        label_source=LabelSource.INFERRED,
    )


def offer_event_dataset(
    session: Session,
    states: Sequence[MarketState],
    *,
    modes: Sequence[str] = ("live",),
    window: timedelta = timedelta(hours=1),
    end: datetime | None = None,
) -> Dataset:
    """Labels from recorded offer lifecycle events. ``live`` => observed; ``paper`` => simulated.

    Offers submitted later than ``end - window`` are excluded because their outcome within the
    window is not yet known (avoids biasing towards "not filled").
    """
    kept, rows = _features_by_time(states)
    state_times = [s.as_of for s in kept]
    events = session.scalars(
        select(FundingOfferEventRow)
        .where(FundingOfferEventRow.mode.in_(list(modes)))
        .order_by(FundingOfferEventRow.event_at)
    ).all()
    first_fill: dict[tuple[str, str], datetime] = {}
    for e in events:
        if e.event in {"partially_filled", "filled"}:
            first_fill.setdefault((e.mode, e.offer_id), e.event_at)
    horizon_end = end or (state_times[-1] if state_times else None)
    times, X, y = [], [], []
    for e in events:
        if e.event != "submitted" or e.rate is None or horizon_end is None:
            continue
        if e.event_at + window > horizon_end:
            continue
        i = bisect_right(state_times, e.event_at) - 1
        if i < 0:
            continue
        s, row = kept[i], rows[i]
        frr = float(s.frr) if s.frr else float("nan")
        premium = float(e.rate) / frr - 1 if not math.isnan(frr) and frr > 0 else float("nan")
        filled_at = first_fill.get((e.mode, e.offer_id))
        times.append(e.event_at)
        X.append([*row, premium, float(e.period_days), math.log(max(float(e.amount), 1e-9))])
        y.append(1.0 if filled_at is not None and filled_at <= e.event_at + window else 0.0)
    source = LabelSource.OBSERVED if set(modes) == {"live"} else LabelSource.SIMULATED
    return Dataset(
        times,
        _matrix(X, len(FeatureBuilder.names()) + len(OFFER_FEATURES)),
        np.array(y, dtype=np.float64),
        FeatureBuilder.names() + OFFER_FEATURES,
        window,
        label_source=source,
    )
