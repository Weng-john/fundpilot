from __future__ import annotations

import dataclasses
import math
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.backtesting.experiment import (
    ExperimentConfig,
    LeakageError,
    StrategySpec,
    check_leakage,
    run_experiment,
)
from fundpilot.backtesting.feed import FeedConfig, MarketFeed
from fundpilot.backtesting.synthetic import SyntheticConfig, generate
from fundpilot.core.models import DataOrigin
from fundpilot.core.portfolio import PortfolioState
from fundpilot.data.db import session_scope
from fundpilot.data.models import FundingOfferEventRow
from fundpilot.ml import datasets, registry
from fundpilot.ml.calibration import IsotonicCalibrator
from fundpilot.ml.evaluate import evaluate_fill, evaluate_forecast, train_and_register
from fundpilot.ml.features import FeatureBuilder
from fundpilot.ml.strategy import FillAwareRateStrategy
from fundpilot.ml.validation import (
    brier,
    log_loss,
    reliability,
    roc_auc,
    walk_forward_splits,
)
from tests.conftest import FIXED_NOW
from tests.helpers import series, state, trade

STEP = timedelta(minutes=10)


@pytest.fixture(scope="module")
def feed() -> MarketFeed:
    t, b, tr = generate(SyntheticConfig(start=FIXED_NOW, days=4, step=STEP, seed=3))
    return MarketFeed(
        FeedConfig(
            exchange="bitfinex",
            symbol="fUST",
            start=FIXED_NOW,
            end=FIXED_NOW + timedelta(days=4) - STEP,
            step=STEP,
            origins=(DataOrigin.SYNTHETIC,),
        ),
        t,
        b,
        tr,
    )


# -- features --------------------------------------------------------------------
def test_features_are_point_in_time() -> None:
    states = series(200, frr="0.0002")
    builder_a, builder_b = FeatureBuilder(), FeatureBuilder()
    a = [builder_a.update(s) for s in states]
    altered = states[:100] + [dataclasses.replace(s, trades=(), book=None) for s in states[100:]]
    b = [builder_b.update(s) for s in altered]
    assert np.array_equal(np.array(a[:100]), np.array(b[:100]), equal_nan=True)
    assert len(a[0]) == len(FeatureBuilder.names())


def test_time_based_windows_are_cadence_independent() -> None:
    fine = series(24 * 60, timedelta(minutes=1), frr="0.0002")
    coarse = fine[::10]
    f1, f2 = FeatureBuilder(), FeatureBuilder()
    for s in fine:
        last_fine = f1.update(s)
    for s in coarse:
        last_coarse = f2.update(s)
    idx = FeatureBuilder.names().index("frr_mean_6h")
    assert last_fine[idx] == pytest.approx(last_coarse[idx])


def test_feature_values() -> None:
    s = state(
        FIXED_NOW,
        trades=[
            trade(FIXED_NOW - timedelta(minutes=5), "0.0003", "100", 30),
            trade(FIXED_NOW - timedelta(minutes=2), "0.0001", "100", 2),
        ],
    )
    row = dict(zip(FeatureBuilder.names(), FeatureBuilder().update(s), strict=True))
    assert row["frr"] == pytest.approx(0.0002)
    assert row["trade_count"] == 2 and row["trade_vwap"] == pytest.approx(0.0002)
    assert row["trade_long_share"] == pytest.approx(0.5)
    assert math.isnan(row["frr_mean_1h"])


# -- validation ------------------------------------------------------------------
def test_walk_forward_purges_overlapping_labels() -> None:
    times = [FIXED_NOW + timedelta(hours=i) for i in range(500)]
    horizon = timedelta(hours=6)
    splits = walk_forward_splits(times, n_splits=4, label_horizon=horizon, min_train=10)
    assert len(splits) == 4
    for sp in splits:
        assert max(times[i] for i in sp.train) + horizon < sp.test_start
        assert set(sp.train.tolist()).isdisjoint(sp.test.tolist())
    assert [len(sp.train) for sp in splits] == sorted(len(sp.train) for sp in splits)


def test_metrics() -> None:
    y = np.array([0, 0, 1, 1], dtype=np.float64)
    p = np.array([0.1, 0.4, 0.35, 0.8])
    assert roc_auc(y, p) == pytest.approx(0.75)
    assert roc_auc(np.ones(3), np.ones(3)) is None
    assert brier(y, y) == 0
    assert log_loss(y, np.full(4, 0.5)) == pytest.approx(math.log(2))
    table, ece = reliability(y, y)
    assert ece == 0 and sum(r["count"] for r in table) == 4


def test_isotonic_is_monotone_and_roundtrips() -> None:
    rng = np.random.default_rng(0)
    s = rng.uniform(size=500)
    y = (rng.uniform(size=500) < s**2).astype(np.float64)
    cal = IsotonicCalibrator().fit(s, y)
    grid = np.linspace(0, 1, 50)
    out = cal.predict(grid)
    assert np.all(np.diff(out) >= 0)
    assert np.array_equal(IsotonicCalibrator.from_dict(cal.to_dict()).predict(grid), out)


# -- datasets and evaluation --------------------------------------------------------
def test_forecast_pipeline(feed: MarketFeed) -> None:
    states = list(feed)
    ds = datasets.forecast_dataset(
        states,
        lambda t: feed.state_at(t).frr,
        timedelta(hours=1),
        sample_every=timedelta(minutes=30),
    )
    assert len(ds) > 100
    assert all(t + ds.label_horizon <= states[-1].as_of + STEP for t in ds.times)
    report = evaluate_forecast(ds, n_splits=3)
    assert set(report.models) == {"persistence", "mean_reversion", "lightgbm"}
    assert report.models["persistence"]["skill_vs_persistence"] == 0
    assert all(0 <= m["interval_coverage_80"] <= 1 for m in report.models.values())


def test_inferred_fill_pipeline_labels_and_registry(feed: MarketFeed, tmp_path: Path) -> None:
    states = list(feed)
    trades = sorted(
        {t.trade_id: t for s in states for t in s.trades}.values(), key=lambda t: t.executed_at
    )
    ds = datasets.inferred_fill_dataset(states, trades, sample_every=timedelta(hours=1))
    assert ds.label_source is datasets.LabelSource.INFERRED
    report = evaluate_fill(ds, n_splits=3)
    d = report.to_dict()
    assert "Not ground truth" in d["label_warning"]
    assert {"binned_empirical", "lightgbm_calibrated"} <= set(report.models)
    directory = train_and_register(ds, "fill_probability", report, tmp_path, {"x": 1})
    again = train_and_register(ds, "fill_probability", report, tmp_path, {"x": 1})
    assert directory == again  # content-addressed version
    meta = registry.load_metadata(directory)
    assert meta["label_source"] == "inferred" and meta["feature_importance_gain"]
    assert (directory / "model.txt").exists() and (directory / "calibrator.json").exists()

    strategy = FillAwareRateStrategy(directory)
    d1 = strategy.decide(states[-1], PortfolioState(states[-1].as_of, "UST", Decimal(5000)))
    assert d1.place == () or d1.place[0].rate is not None
    # leakage guard: model trained through the end of the feed
    with pytest.raises(LeakageError):
        check_leakage(strategy, FIXED_NOW + timedelta(days=1))
    check_leakage(strategy, FIXED_NOW + timedelta(days=10))

    cfg = ExperimentConfig.from_dict(
        {
            "name": "leak",
            "start": (FIXED_NOW + timedelta(days=1)).isoformat(),
            "end": (FIXED_NOW + timedelta(days=2)).isoformat(),
            "step_seconds": 600,
            "scenarios": ["neutral"],
            "strategies": [{"id": "fa", "type": "fill_aware", "model_dir": str(directory)}],
        }
    )
    assert cfg.strategies[0] == StrategySpec("fa", "fill_aware", {"model_dir": str(directory)})
    with pytest.raises(LeakageError):
        run_experiment(cfg, feed, feed.provenance, now=FIXED_NOW)


def test_offer_event_dataset_label_sources(
    sessions: sessionmaker[Session], feed: MarketFeed
) -> None:
    t0 = FIXED_NOW + timedelta(days=1)
    rows = []
    for i in range(6):
        at = t0 + timedelta(hours=i)
        base = {
            "account": "a",
            "mode": "paper",
            "exchange": "bitfinex",
            "offer_id": f"o{i}",
            "client_ref": None,
            "symbol": "fUST",
            "amount": Decimal(1000),
            "amount_orig": None,
            "rate": Decimal("0.0002"),
            "period_days": 2,
            "status_raw": None,
        }
        rows.append(FundingOfferEventRow(**base, event="submitted", event_at=at))
        if i % 2 == 0:
            rows.append(
                FundingOfferEventRow(
                    **{**base, "amount": Decimal(0)},
                    event="filled",
                    event_at=at + timedelta(minutes=10),
                )
            )
    with session_scope(sessions) as s:
        s.add_all(rows)
    with session_scope(sessions) as s:
        paper = datasets.offer_event_dataset(s, list(feed), modes=("paper",))
        live = datasets.offer_event_dataset(s, list(feed), modes=("live",))
    assert paper.label_source is datasets.LabelSource.SIMULATED
    assert paper.y.tolist() == [1, 0, 1, 0, 1, 0]
    assert live.label_source is datasets.LabelSource.OBSERVED and len(live) == 0
