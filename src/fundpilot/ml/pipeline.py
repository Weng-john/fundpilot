"""Config-driven ML runs used by the CLI (``fundpilot ml forecast|fill``)."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from fundpilot.backtesting.feed import FeedConfig, MarketFeed, load_feed
from fundpilot.backtesting.synthetic import SyntheticConfig, generate
from fundpilot.core.models import DataOrigin
from fundpilot.core.time import ensure_utc, utc_now
from fundpilot.ml import datasets
from fundpilot.ml.evaluate import (
    EvaluationReport,
    evaluate_fill,
    evaluate_forecast,
    train_and_register,
)


@dataclass(frozen=True, slots=True)
class MLConfig:
    name: str
    task: str  # "rate_forecast" | "fill_probability"
    exchange: str
    symbol: str
    start: datetime
    end: datetime
    step: timedelta
    horizon: timedelta
    sample_every: timedelta
    n_splits: int
    label_source: str  # "inferred" | "observed" | "simulated"
    allow_synthetic: bool
    register: bool
    models_dir: Path
    raw: dict[str, Any]

    @classmethod
    def from_toml(cls, path: Path) -> MLConfig:
        raw = tomllib.loads(path.read_text())
        task = raw["task"]
        if task not in {"rate_forecast", "fill_probability"}:
            raise ValueError(f"unknown task {task!r}")
        return cls(
            name=raw["name"],
            task=task,
            exchange=raw.get("exchange", "bitfinex"),
            symbol=raw.get("symbol", "fUST"),
            start=ensure_utc(datetime.fromisoformat(raw["start"])),
            end=ensure_utc(datetime.fromisoformat(raw["end"])),
            step=timedelta(seconds=int(raw.get("step_seconds", 60))),
            horizon=timedelta(seconds=int(raw.get("horizon_seconds", 3600))),
            sample_every=timedelta(seconds=int(raw.get("sample_every_seconds", 3600))),
            n_splits=int(raw.get("n_splits", 4)),
            label_source=raw.get("label_source", "inferred"),
            allow_synthetic=bool(raw.get("allow_synthetic", False)),
            register=bool(raw.get("register", True)),
            models_dir=Path(raw.get("models_dir", "models")),
            raw=raw,
        )


def _feed(cfg: MLConfig, session: Session | None, synthetic_demo: bool) -> MarketFeed:
    origins = (
        (DataOrigin.EXCHANGE_API, DataOrigin.SYNTHETIC)
        if (cfg.allow_synthetic or synthetic_demo)
        else (DataOrigin.EXCHANGE_API,)
    )
    fc = FeedConfig(
        exchange=cfg.exchange,
        symbol=cfg.symbol,
        start=cfg.start,
        end=cfg.end,
        step=cfg.step,
        origins=origins,
    )
    if synthetic_demo:
        days = max(1, int((cfg.end - cfg.start).total_seconds() // 86_400) + 1)
        t, b, tr = generate(
            SyntheticConfig(symbol=cfg.symbol, start=cfg.start, days=days, step=cfg.step)
        )
        return MarketFeed(fc, t, b, tr)
    if session is None:
        raise ValueError("a database session is required unless synthetic_demo is used")
    return load_feed(session, fc)


def run(
    cfg: MLConfig,
    session: Session | None,
    *,
    synthetic_demo: bool = False,
    output: Path | None = None,
) -> tuple[EvaluationReport, Path | None, Path]:
    feed = _feed(cfg, session, synthetic_demo)
    states = list(feed)
    synthetic = synthetic_demo or any(o != "exchange_api" for o in feed.provenance.origins)
    if cfg.task == "rate_forecast":
        ds = datasets.forecast_dataset(
            states,
            lambda t: feed.state_at(t).frr if not feed.state_at(t).stale else None,
            cfg.horizon,
            sample_every=cfg.sample_every,
        )
        report = evaluate_forecast(ds, n_splits=cfg.n_splits)
    else:
        if cfg.label_source == "inferred":
            trades = sorted(
                {t.trade_id: t for s in states for t in s.trades}.values(),
                key=lambda t: t.executed_at,
            )
            ds = datasets.inferred_fill_dataset(
                states, trades, window=cfg.horizon, sample_every=cfg.sample_every
            )
        else:
            if session is None:
                raise ValueError("offer-event labels need the database")
            modes = ("live",) if cfg.label_source == "observed" else ("paper",)
            ds = datasets.offer_event_dataset(session, states, modes=modes, window=cfg.horizon)
        report = evaluate_fill(ds, n_splits=cfg.n_splits)
    if synthetic:
        report.notes.insert(0, "SYNTHETIC DATA - NOT MARKET EVIDENCE.")
    model_dir = None
    if cfg.register:
        model_dir = train_and_register(
            ds, cfg.task, report, cfg.models_dir, {**cfg.raw, "synthetic": synthetic}
        )
    out = output or Path("experiments/outputs/ml") / cfg.name / utc_now().strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "report.json"
    path.write_text(
        json.dumps(
            {
                **report.to_dict(),
                "synthetic": synthetic,
                "model_dir": None if model_dir is None else str(model_dir),
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    return report, model_dir, path
