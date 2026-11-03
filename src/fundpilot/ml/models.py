"""Model implementations: statistical baselines and LightGBM, with a common interface."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from fundpilot.ml.calibration import IsotonicCalibrator
from fundpilot.ml.validation import FloatArray

LGBM_BASE: dict[str, Any] = {
    "verbosity": -1,
    "num_threads": 1,
    "deterministic": True,
    "force_row_wise": True,
    "seed": 0,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.9,
    "bagging_fraction": 1.0,
}


def _lgb() -> Any:
    try:
        import lightgbm  # noqa: PLC0415 - optional dependency
    except ImportError as exc:  # pragma: no cover - depends on installed extras
        raise RuntimeError("LightGBM is not installed: `uv sync --extra ml`") from exc
    return lightgbm


# ---------------------------------------------------------------------------
# Rate forecasting (targets are log(rate_{t+h} / rate_t))
# ---------------------------------------------------------------------------
class Forecaster(Protocol):
    name: str

    def fit(self, X: FloatArray, y: FloatArray) -> None: ...

    def predict(self, X: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Point forecast and a (10 %, 90 %) prediction interval, all in target space."""
        ...


def _quantiles(residuals: FloatArray) -> tuple[float, float]:
    return float(np.quantile(residuals, 0.1)), float(np.quantile(residuals, 0.9))


@dataclass(slots=True)
class PersistenceForecaster:
    """Tomorrow's rate equals today's; interval from training residual quantiles."""

    name: str = "persistence"
    q: tuple[float, float] = (0.0, 0.0)

    def fit(self, X: FloatArray, y: FloatArray) -> None:
        self.q = _quantiles(y)

    def predict(self, X: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
        zero = np.zeros(len(X))
        return zero, zero + self.q[0], zero + self.q[1]


@dataclass(slots=True)
class MeanReversionForecaster:
    """Linear regression of the target on one deviation feature (e.g. log(frr / 24h mean))."""

    feature_index: int
    name: str = "mean_reversion"
    coef: tuple[float, float] = (0.0, 0.0)
    q: tuple[float, float] = (0.0, 0.0)

    def _x(self, X: FloatArray) -> FloatArray:
        col: FloatArray = np.nan_to_num(X[:, self.feature_index], nan=0.0)
        return col

    def fit(self, X: FloatArray, y: FloatArray) -> None:
        x = self._x(X)
        A = np.column_stack([np.ones(len(x)), x])
        sol, *_ = np.linalg.lstsq(A, y, rcond=None)
        self.coef = (float(sol[0]), float(sol[1]))
        self.q = _quantiles(y - (self.coef[0] + self.coef[1] * x))

    def predict(self, X: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
        p = self.coef[0] + self.coef[1] * self._x(X)
        return p, p + self.q[0], p + self.q[1]


@dataclass(slots=True)
class LightGBMForecaster:
    num_boost_round: int = 200
    name: str = "lightgbm"
    boosters: dict[str, Any] = field(default_factory=dict)

    def fit(self, X: FloatArray, y: FloatArray) -> None:
        lgb = _lgb()
        objectives: dict[str, dict[str, Any]] = {
            "point": {"objective": "regression"},
            "q10": {"objective": "quantile", "alpha": 0.1},
            "q90": {"objective": "quantile", "alpha": 0.9},
        }
        for key, extra in objectives.items():
            ds = lgb.Dataset(X, label=y, free_raw_data=False)
            self.boosters[key] = lgb.train(
                {**LGBM_BASE, **extra}, ds, num_boost_round=self.num_boost_round
            )

    def predict(self, X: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
        p, lo, hi = (
            np.asarray(self.boosters[k].predict(X), dtype=np.float64)
            for k in ("point", "q10", "q90")
        )
        return p, np.minimum(lo, hi), np.maximum(lo, hi)

    def importance(self, names: list[str]) -> dict[str, float]:
        gains = self.boosters["point"].feature_importance(importance_type="gain")
        return dict(
            sorted(zip(names, (float(g) for g in gains), strict=True), key=lambda kv: -kv[1])
        )


# ---------------------------------------------------------------------------
# Fill probability
# ---------------------------------------------------------------------------
class FillModel(Protocol):
    name: str

    def fit(self, X: FloatArray, y: FloatArray) -> None: ...

    def predict_proba(self, X: FloatArray) -> FloatArray: ...


@dataclass(slots=True)
class BinnedFillModel:
    """Empirical fill frequency by (rate-premium bin, period), Laplace-smoothed."""

    premium_index: int
    period_index: int
    edges: tuple[float, ...] = (-0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.4)
    alpha: float = 1.0
    name: str = "binned_empirical"
    table: dict[str, float] = field(default_factory=dict)
    prior: float = 0.5

    def _keys(self, X: FloatArray) -> list[str]:
        bins = np.digitize(np.nan_to_num(X[:, self.premium_index]), self.edges)
        return [f"{int(b)}|{int(p)}" for b, p in zip(bins, X[:, self.period_index], strict=True)]

    def fit(self, X: FloatArray, y: FloatArray) -> None:
        self.prior = float(np.mean(y)) if len(y) else 0.5
        counts: dict[str, list[float]] = {}
        for key, label in zip(self._keys(X), y, strict=True):
            c = counts.setdefault(key, [0.0, 0.0])
            c[0] += float(label)
            c[1] += 1
        self.table = {
            k: (s + self.alpha * self.prior) / (n + self.alpha) for k, (s, n) in counts.items()
        }

    def predict_proba(self, X: FloatArray) -> FloatArray:
        return np.array([self.table.get(k, self.prior) for k in self._keys(X)], dtype=np.float64)

    def to_dict(self) -> dict[str, Any]:
        return {
            "premium_index": self.premium_index,
            "period_index": self.period_index,
            "edges": list(self.edges),
            "alpha": self.alpha,
            "table": self.table,
            "prior": self.prior,
        }


@dataclass(slots=True)
class LightGBMFillModel:
    """LightGBM classifier + isotonic calibration on the most recent ``calibration_share``."""

    num_boost_round: int = 200
    calibration_share: float = 0.2
    name: str = "lightgbm_calibrated"
    booster: Any = None
    calibrator: IsotonicCalibrator | None = None

    def fit(self, X: FloatArray, y: FloatArray) -> None:
        lgb = _lgb()
        n_cal = max(1, int(len(y) * self.calibration_share)) if len(y) >= 50 else 0
        fit_X, fit_y = (X[:-n_cal], y[:-n_cal]) if n_cal else (X, y)
        self.booster = lgb.train(
            {**LGBM_BASE, "objective": "binary"},
            lgb.Dataset(fit_X, label=fit_y),
            num_boost_round=self.num_boost_round,
        )
        if n_cal:
            raw = np.asarray(self.booster.predict(X[-n_cal:]), dtype=np.float64)
            self.calibrator = IsotonicCalibrator().fit(raw, y[-n_cal:])

    def raw_proba(self, X: FloatArray) -> FloatArray:
        out: FloatArray = np.asarray(self.booster.predict(X), dtype=np.float64)
        return out

    def predict_proba(self, X: FloatArray) -> FloatArray:
        raw = self.raw_proba(X)
        return raw if self.calibrator is None else self.calibrator.predict(raw)

    def importance(self, names: list[str]) -> dict[str, float]:
        gains = self.booster.feature_importance(importance_type="gain")
        return dict(
            sorted(zip(names, (float(g) for g in gains), strict=True), key=lambda kv: -kv[1])
        )

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(directory / "model.txt"))
        if self.calibrator is not None:
            (directory / "calibrator.json").write_text(json.dumps(self.calibrator.to_dict()))

    @classmethod
    def load(cls, directory: Path) -> LightGBMFillModel:
        lgb = _lgb()
        model = cls(booster=lgb.Booster(model_file=str(directory / "model.txt")))
        cal = directory / "calibrator.json"
        if cal.exists():
            model.calibrator = IsotonicCalibrator.from_dict(json.loads(cal.read_text()))
        return model
