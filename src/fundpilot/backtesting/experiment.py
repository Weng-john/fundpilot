"""Reproducible experiments: strategies x execution scenarios on one point-in-time feed.

An experiment is fully described by a TOML file plus the stored data it reads; the
seed makes stochastic parts (early repayment) deterministic.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fundpilot.backtesting.feed import FeedProvenance
from fundpilot.backtesting.metrics import Metrics, compute_metrics, excess_return
from fundpilot.backtesting.scenarios import PRESETS, ExecutionScenario
from fundpilot.backtesting.simulator import FundingSimulator, SimulationConfig
from fundpilot.core.time import ensure_utc
from fundpilot.strategies import (
    AdaptiveFundingLadder,
    AutoRenewStrategy,
    FixedRateStrategy,
    FRRStrategy,
    OrderBookAwareStrategy,
)
from fundpilot.strategies.base import MarketState, Strategy
from fundpilot.strategies.constraints import FundingConstraints

StrategyFactory = Callable[[dict[str, Any]], Strategy]


def _dec(params: dict[str, Any], key: str, default: str | None = None) -> Decimal | None:
    value = params.get(key, default)
    return None if value is None else Decimal(str(value))


def _req_dec(params: dict[str, Any], key: str, default: str) -> Decimal:
    return Decimal(str(params.get(key, default)))


STRATEGY_TYPES: dict[str, StrategyFactory] = {
    "fixed_rate": lambda p: FixedRateStrategy(
        rate=_req_dec(p, "rate", "0.0002"),
        period_days=int(p.get("period_days", 2)),
        max_offer_size=_dec(p, "max_offer_size"),
    ),
    "frr": lambda p: FRRStrategy(
        period_days=int(p.get("period_days", 2)), max_offer_size=_dec(p, "max_offer_size")
    ),
    "auto_renew": lambda p: AutoRenewStrategy(
        period_days=int(p.get("period_days", 2)),
        rate=_dec(p, "rate"),
        keep_amount=_req_dec(p, "keep_amount", "0"),
    ),
    "adaptive_ladder": lambda p: AdaptiveFundingLadder(
        tilt=_req_dec(p, "tilt", "0.8"),
        reserve_fraction=_req_dec(p, "reserve_fraction", "0"),
        rate_quantile=_req_dec(p, "rate_quantile", "0.5"),
        reprice_after=timedelta(minutes=float(p.get("reprice_after_minutes", 60))),
    ),
    "orderbook_aware": lambda p: OrderBookAwareStrategy(
        period_days=int(p.get("period_days", 2)),
        depth_ahead=_req_dec(p, "depth_ahead", "500000"),
        reprice_after=timedelta(minutes=float(p.get("reprice_after_minutes", 30))),
    ),
}


def _fill_aware(p: dict[str, Any]) -> Strategy:
    from fundpilot.ml.strategy import FillAwareRateStrategy  # noqa: PLC0415 - optional extra

    return FillAwareRateStrategy(
        model_dir=Path(str(p["model_dir"])),
        period_days=int(p.get("period_days", 2)),
        min_probability=float(p.get("min_probability", 0.2)),
    )


STRATEGY_TYPES["fill_aware"] = _fill_aware


class LeakageError(ValueError):
    """A learned strategy's training data overlaps the evaluation window."""


def check_leakage(strategy: Strategy, start: datetime) -> None:
    training_end = getattr(strategy, "training_end", None)
    if isinstance(training_end, datetime) and training_end >= start:
        raise LeakageError(
            f"{strategy.name} was trained on data up to {training_end.isoformat()}, which is "
            f"not before the backtest start {start.isoformat()}; use a later window"
        )


@dataclass(frozen=True, slots=True)
class StrategySpec:
    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def build(self) -> Strategy:
        try:
            factory = STRATEGY_TYPES[self.type]
        except KeyError:
            raise ValueError(f"unknown strategy type {self.type!r}") from None
        return factory(self.params)


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    name: str
    exchange: str
    symbol: str
    start: datetime
    end: datetime
    step: timedelta
    strategies: tuple[StrategySpec, ...]
    scenarios: tuple[ExecutionScenario, ...]
    baseline: str
    simulation: SimulationConfig
    allow_synthetic: bool = False

    @classmethod
    def from_toml(cls, path: Path) -> ExperimentConfig:
        return cls.from_dict(tomllib.loads(path.read_text()))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ExperimentConfig:
        sim = raw.get("simulation", {})
        strategies = tuple(
            StrategySpec(
                s["id"], s["type"], {k: v for k, v in s.items() if k not in {"id", "type"}}
            )
            for s in raw["strategies"]
        )
        ids = [s.id for s in strategies]
        if len(set(ids)) != len(ids):
            raise ValueError("strategy ids must be unique")
        baseline = raw.get("baseline", ids[0])
        if baseline not in ids:
            raise ValueError(f"baseline {baseline!r} is not one of the strategies")
        return cls(
            name=raw["name"],
            exchange=raw.get("exchange", "bitfinex"),
            symbol=raw.get("symbol", "fUST"),
            start=_utc(raw["start"]),
            end=_utc(raw["end"]),
            step=timedelta(seconds=int(raw.get("step_seconds", 60))),
            strategies=strategies,
            scenarios=tuple(PRESETS[name] for name in raw.get("scenarios", list(PRESETS))),
            baseline=baseline,
            simulation=SimulationConfig(
                initial_capital=Decimal(str(sim.get("initial_capital", "10000"))),
                currency=sim.get("currency", "UST"),
                decision_interval=timedelta(seconds=int(sim.get("decision_interval_seconds", 300))),
                seed=int(raw.get("seed", 0)),
                operating_cost_per_day=Decimal(str(sim.get("operating_cost_per_day", "0"))),
            ),
            allow_synthetic=bool(raw.get("allow_synthetic", False)),
        )


def _utc(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return ensure_utc(dt)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    strategy_id: str
    strategy_name: str
    scenario: str
    metrics: Metrics
    excess_vs_baseline: Decimal | None
    rejections: int


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    config: ExperimentConfig
    provenance: FeedProvenance
    data_label: str  # "exchange_api" or "SYNTHETIC"
    runs: tuple[RunOutcome, ...]
    created_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "simulated",
            "data_label": self.data_label,
            "warning": _banner(self.data_label),
            "experiment": self.config.name,
            "created_at": self.created_at.isoformat(),
            "symbol": self.config.symbol,
            "window": [self.config.start.isoformat(), self.config.end.isoformat()],
            "baseline": self.config.baseline,
            "seed": self.config.simulation.seed,
            "provenance": {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in asdict(self.provenance).items()
            },
            "scenarios": {
                s.name: {k: str(v) for k, v in asdict(s).items()} for s in self.config.scenarios
            },
            "runs": [
                {
                    "strategy_id": r.strategy_id,
                    "strategy": r.strategy_name,
                    "scenario": r.scenario,
                    "rejections": r.rejections,
                    "excess_vs_baseline": None
                    if r.excess_vs_baseline is None
                    else str(r.excess_vs_baseline),
                    "metrics": r.metrics.as_dict(),
                }
                for r in self.runs
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Experiment: {self.config.name}",
            "",
            f"> **{_banner(self.data_label)}**",
            "",
            f"- Symbol: `{self.config.symbol}`; window {self.config.start.isoformat()} → "
            f"{self.config.end.isoformat()}; step {self.config.step}",
            f"- Data: {self.provenance.tickers} tickers, {self.provenance.books} books, "
            f"{self.provenance.trades} trades; origins {', '.join(self.provenance.origins)}",
            f"- Baseline: `{self.config.baseline}`; seed {self.config.simulation.seed}; "
            f"initial capital {self.config.simulation.initial_capital}",
            "",
            "| scenario | strategy | net ann. yield | after costs | excess vs baseline | "
            "utilisation | idle | fill ratio | median first fill (min) | early repay |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in self.runs:
            m = r.metrics
            ttf = (
                "-"
                if m.median_time_to_first_fill_seconds is None
                else f"{m.median_time_to_first_fill_seconds / 60:.1f}"
            )
            exc = "-" if r.excess_vs_baseline is None else _pct(r.excess_vs_baseline)
            lines.append(
                f"| {r.scenario} | {r.strategy_id} | {_pct(m.net_annualized_yield)} | "
                f"{_pct(m.net_annualized_yield_after_costs)} | {exc} | "
                f"{_pct(m.capital_utilization)} | {_pct(m.idle_capital_ratio)} | "
                f"{_pct(m.fill_ratio)} | {ttf} | {m.early_repayments} |"
            )
        lines += [
            "",
            "Execution assumptions per scenario are listed in the JSON report. "
            "None are calibrated against observed fills.",
        ]
        return "\n".join(lines) + "\n"


def _pct(x: Decimal) -> str:
    return f"{x * 100:.3f}%"


def _banner(label: str) -> str:
    if label == "exchange_api":
        return (
            "SIMULATED results on recorded exchange data under assumed execution "
            "scenarios. Not realised returns."
        )
    return (
        "SYNTHETIC DATA — NOT MARKET EVIDENCE. Simulated results on generated data; "
        "useful only to exercise the pipeline."
    )


def run_experiment(
    config: ExperimentConfig,
    feed: Iterable[MarketState],
    provenance: FeedProvenance,
    *,
    constraints: FundingConstraints | None = None,
    now: datetime,
) -> ExperimentReport:
    states = list(feed)
    if len(states) < 2:
        raise ValueError("feed has fewer than two market states; is there data in the window?")
    if all(s.stale for s in states):
        raise ValueError("every market state is stale; no usable observations in the window")
    synthetic = any(o != "exchange_api" for o in provenance.origins)
    runs: list[RunOutcome] = []
    for scenario in config.scenarios:
        metrics_by_id: dict[str, tuple[str, Metrics, int]] = {}
        for spec in config.strategies:
            strategy = spec.build()  # fresh instance: strategies may be stateful
            check_leakage(strategy, config.start)
            result = FundingSimulator(strategy, scenario, config.simulation, constraints).run(
                states
            )
            metrics_by_id[spec.id] = (
                strategy.name,
                compute_metrics(result),
                len(result.rejections),
            )
        base = metrics_by_id[config.baseline][1]
        for spec in config.strategies:
            name, metrics, rejections = metrics_by_id[spec.id]
            excess = None if spec.id == config.baseline else excess_return(metrics, base)
            runs.append(RunOutcome(spec.id, name, scenario.name, metrics, excess, rejections))
    return ExperimentReport(
        config, provenance, "SYNTHETIC" if synthetic else "exchange_api", tuple(runs), now
    )


def write_report(report: ExperimentReport, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    md_path = directory / "report.md"
    json_path.write_text(json.dumps(report.to_json(), indent=2) + "\n")
    md_path.write_text(report.to_markdown())
    return json_path, md_path
