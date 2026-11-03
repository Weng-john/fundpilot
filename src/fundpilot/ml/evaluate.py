"""Walk-forward evaluation and final training for both prediction tasks.

Each model is refit on every expanding training window (purged of rows whose labels
overlap the test block) and scored only on the following unseen block. Results are
out-of-sample by construction and are compared against simple baselines.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from fundpilot.ml import registry
from fundpilot.ml.datasets import LABEL_WARNINGS, Dataset, LabelSource
from fundpilot.ml.models import (
    BinnedFillModel,
    FillModel,
    Forecaster,
    LightGBMFillModel,
    LightGBMForecaster,
    MeanReversionForecaster,
    PersistenceForecaster,
)
from fundpilot.ml.validation import (
    FloatArray,
    brier,
    interval_coverage,
    log_loss,
    mae,
    reliability,
    rmse,
    roc_auc,
    walk_forward_splits,
)


@dataclass(slots=True)
class EvaluationReport:
    task: str
    rows: int
    splits: int
    models: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    label_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "rows": self.rows,
            "walk_forward_splits": self.splits,
            "evaluation": "out-of-sample walk-forward",
            "label_source": self.label_source,
            "label_warning": None
            if self.label_source is None
            else LABEL_WARNINGS[LabelSource(self.label_source)],
            "models": self.models,
            "notes": self.notes,
        }


def _splits(ds: Dataset, n_splits: int, embargo: timedelta, min_train: int) -> list[Any]:
    splits = walk_forward_splits(
        ds.times,
        n_splits=n_splits,
        label_horizon=ds.label_horizon,
        embargo=embargo,
        min_train=min_train,
    )
    if not splits:
        raise ValueError(f"dataset with {len(ds)} rows is too small for walk-forward evaluation")
    return splits


# ---------------------------------------------------------------------------
# Task 1
# ---------------------------------------------------------------------------
def forecast_models(
    feature_names: list[str], with_lightgbm: bool = True
) -> list[Callable[[], Forecaster]]:
    idx = feature_names.index("frr_logratio_mean_24h")
    makers: list[Callable[[], Forecaster]] = [
        PersistenceForecaster,
        lambda: MeanReversionForecaster(idx),
    ]
    if with_lightgbm:
        makers.append(LightGBMForecaster)
    return makers


def evaluate_forecast(
    ds: Dataset,
    *,
    n_splits: int = 4,
    embargo: timedelta = timedelta(0),
    min_train: int = 50,
    with_lightgbm: bool = True,
) -> EvaluationReport:
    assert ds.current_rate is not None  # noqa: S101 - forecast datasets always carry it
    splits = _splits(ds, n_splits, embargo, min_train)
    report = EvaluationReport("rate_forecast", len(ds), len(splits))
    preds: dict[str, list[FloatArray]] = {}
    truth: list[FloatArray] = []
    for sp in splits:
        cur = ds.current_rate[sp.test]
        truth.append(cur * np.exp(ds.y[sp.test]))
        for make in forecast_models(ds.feature_names, with_lightgbm):
            m = make()
            m.fit(ds.X[sp.train], ds.y[sp.train])
            p, lo, hi = m.predict(ds.X[sp.test])
            preds.setdefault(m.name, []).append(
                np.stack([cur * np.exp(p), cur * np.exp(lo), cur * np.exp(hi)])
            )
    y = np.concatenate(truth)
    base_mae = None
    for name, chunks in preds.items():
        arr = np.concatenate(chunks, axis=1)
        m_mae = mae(y, arr[0])
        if name == "persistence":
            base_mae = m_mae
        report.models[name] = {
            "mae": m_mae,
            "rmse": rmse(y, arr[0]),
            "interval_coverage_80": interval_coverage(y, arr[1], arr[2]),
            "mean_interval_width": float(np.mean(arr[2] - arr[1])),
        }
    for metrics in report.models.values():
        metrics["skill_vs_persistence"] = None if not base_mae else 1 - metrics["mae"] / base_mae
    report.notes.append("Target: FRR at t+horizon (daily fraction); intervals nominal 80 %.")
    return report


# ---------------------------------------------------------------------------
# Task 2
# ---------------------------------------------------------------------------
def fill_models(
    feature_names: list[str], with_lightgbm: bool = True
) -> list[Callable[[], FillModel]]:
    pi, qi = feature_names.index("offer_premium"), feature_names.index("offer_period")
    makers: list[Callable[[], FillModel]] = [lambda: BinnedFillModel(pi, qi)]
    if with_lightgbm:
        makers.append(LightGBMFillModel)
    return makers


def evaluate_fill(
    ds: Dataset,
    *,
    n_splits: int = 4,
    embargo: timedelta = timedelta(0),
    min_train: int = 50,
    with_lightgbm: bool = True,
) -> EvaluationReport:
    splits = _splits(ds, n_splits, embargo, min_train)
    report = EvaluationReport(
        "fill_probability",
        len(ds),
        len(splits),
        label_source=None if ds.label_source is None else ds.label_source.value,
    )
    preds: dict[str, list[FloatArray]] = {}
    truth: list[FloatArray] = []
    for sp in splits:
        truth.append(ds.y[sp.test])
        for make in fill_models(ds.feature_names, with_lightgbm):
            m = make()
            m.fit(ds.X[sp.train], ds.y[sp.train])
            preds.setdefault(m.name, []).append(m.predict_proba(ds.X[sp.test]))
    y = np.concatenate(truth)
    base_rate = float(np.mean(y))
    report.models["constant_base_rate"] = {
        "brier": brier(y, np.full(len(y), base_rate)),
        "note": "uses the test base rate (optimistic)",
    }
    for name, chunks in preds.items():
        p = np.concatenate(chunks)
        table, ece = reliability(y, p)
        report.models[name] = {
            "brier": brier(y, p),
            "log_loss": log_loss(y, p),
            "auc": roc_auc(y, p),
            "ece": ece,
            "reliability": table,
        }
    report.notes.append(f"Test base rate {base_rate:.4f} over {len(y)} rows.")
    return report


# ---------------------------------------------------------------------------
# Final training + registry
# ---------------------------------------------------------------------------
def train_and_register(
    ds: Dataset, task: str, report: EvaluationReport, root: Path, config: dict[str, Any]
) -> Path:
    """Fit the LightGBM model on all rows and store it with its OOS evaluation."""
    identity = {"task": task, "config": config, "data": ds.fingerprint()}
    meta: dict[str, Any] = {
        "training_window": [ds.times[0].isoformat(), ds.times[-1].isoformat()],
        "label_horizon_seconds": ds.label_horizon.total_seconds(),
        "rows": len(ds),
        "feature_names": ds.feature_names,
        "label_source": None if ds.label_source is None else ds.label_source.value,
        "out_of_sample_evaluation": report.to_dict(),
    }
    if task == "fill_probability":
        fill = LightGBMFillModel()
        fill.fit(ds.X, ds.y)
        meta["model_type"] = fill.name
        meta["feature_importance_gain"] = fill.importance(ds.feature_names)
        directory = registry.save(root, task, identity, meta)
        fill.save(directory)
    else:
        fc = LightGBMForecaster()
        fc.fit(ds.X, ds.y)
        meta["model_type"] = fc.name
        meta["feature_importance_gain"] = fc.importance(ds.feature_names)
        directory = registry.save(root, task, identity, meta)
        for key, booster in fc.boosters.items():
            booster.save_model(str(directory / f"{key}.txt"))
    (directory / "evaluation.json").write_text(
        json.dumps(report.to_dict(), indent=2, default=str) + "\n"
    )
    return directory
