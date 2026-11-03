"""Train RL policies on an early window and compare them with baseline strategies on a later,
unseen window under identical simulation assumptions (same scenario, constraints, risk gate).
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fundpilot.backtesting.account import SimulationConfig
from fundpilot.backtesting.experiment import StrategySpec
from fundpilot.backtesting.feed import MarketFeed
from fundpilot.backtesting.metrics import compute_metrics, excess_return
from fundpilot.backtesting.scenarios import PRESETS, ExecutionScenario
from fundpilot.backtesting.simulator import FundingSimulator
from fundpilot.core.time import ensure_utc
from fundpilot.risk.manager import RiskManager
from fundpilot.rl import ppo
from fundpilot.rl.env import FundingEnv, RewardConfig
from fundpilot.rl.policies import ConstantPolicy, Policy, evaluate_policy, train_cem
from fundpilot.rl.strategy import PolicyStrategy
from fundpilot.strategies.base import MarketState, Strategy


@dataclass(frozen=True, slots=True)
class RLConfig:
    name: str
    symbol: str
    start: datetime
    end: datetime
    step: timedelta
    train_fraction: float
    scenarios: tuple[ExecutionScenario, ...]
    simulation: SimulationConfig
    decision_every: int
    episode_decisions: int
    warmup: int
    reward: RewardConfig
    cem: dict[str, Any]
    ppo_timesteps: int
    baselines: tuple[StrategySpec, ...]
    baseline_id: str
    seed: int

    @classmethod
    def from_toml(cls, path: Path) -> RLConfig:
        return cls.from_dict(tomllib.loads(path.read_text()))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RLConfig:
        step = timedelta(seconds=int(raw.get("step_seconds", 60)))
        decision_every = int(raw.get("decision_every", 5))
        sim = raw.get("simulation", {})
        baselines = tuple(
            StrategySpec(
                s["id"], s["type"], {k: v for k, v in s.items() if k not in {"id", "type"}}
            )
            for s in raw.get("baselines", [{"id": "frr", "type": "frr"}])
        )
        return cls(
            name=raw["name"],
            symbol=raw.get("symbol", "fUST"),
            start=ensure_utc(datetime.fromisoformat(raw["start"])),
            end=ensure_utc(datetime.fromisoformat(raw["end"])),
            step=step,
            train_fraction=float(raw.get("train_fraction", 0.7)),
            scenarios=tuple(PRESETS[n] for n in raw.get("scenarios", ["conservative"])),
            simulation=SimulationConfig(
                initial_capital=Decimal(str(sim.get("initial_capital", "10000"))),
                decision_interval=step * decision_every,
                seed=int(raw.get("seed", 0)),
            ),
            decision_every=decision_every,
            episode_decisions=int(raw.get("episode_decisions", 96)),
            warmup=int(raw.get("warmup_steps", 288)),
            reward=RewardConfig(**raw.get("reward", {})),
            cem=dict(raw.get("cem", {})),
            ppo_timesteps=int(raw.get("ppo_timesteps", 0)),
            baselines=baselines,
            baseline_id=raw.get("baseline", baselines[0].id),
            seed=int(raw.get("seed", 0)),
        )


def _backtest(
    strategy: Strategy,
    warm: list[MarketState],
    test: list[MarketState],
    scenario: ExecutionScenario,
    cfg: RLConfig,
) -> Any:
    if isinstance(strategy, PolicyStrategy):
        for s in warm:
            strategy.observe_only(s)
    sim = FundingSimulator(strategy, scenario, cfg.simulation, gate=RiskManager().as_gate())
    return compute_metrics(sim.run(test))


def run(
    cfg: RLConfig, feed: MarketFeed, *, synthetic: bool, output: Path | None = None
) -> dict[str, Any]:
    states = list(feed)
    split = int(len(states) * cfg.train_fraction)
    train, test = states[:split], states[split:]
    warm = states[max(0, split - cfg.warmup) : split]
    scenario0 = cfg.scenarios[0]

    def env(window: list[MarketState], *, random_start: bool) -> FundingEnv:
        episode = min(cfg.episode_decisions, (len(window) - cfg.warmup - 1) // cfg.decision_every)
        return FundingEnv(
            window,
            scenario0,
            cfg.simulation,
            episode_decisions=episode,
            decision_every=cfg.decision_every,
            warmup=cfg.warmup,
            reward=cfg.reward,
            random_start=random_start,
        )

    train_env = env(train, random_start=True)
    policies: dict[str, Policy] = {
        "constant_frr_2d_all": ConstantPolicy((1, 0, 3, 0), name="constant_frr_2d_all"),
    }
    cem_policy, cem_curve = train_cem(train_env, seed=cfg.seed, **cfg.cem)
    policies["cem"] = cem_policy
    notes = []
    if cfg.ppo_timesteps > 0:
        if ppo.available():
            policies["ppo"] = ppo.train_ppo(train_env, timesteps=cfg.ppo_timesteps, seed=cfg.seed)
        else:
            notes.append("PPO skipped: stable-baselines3 not installed (uv sync --extra rl)")

    test_env = env(states[max(0, split - cfg.warmup) :], random_start=False)
    env_eval = {name: evaluate_policy(test_env, p, [cfg.seed]) for name, p in policies.items()}

    runs = []
    for scenario in cfg.scenarios:
        metrics: dict[str, Any] = {}
        for spec in cfg.baselines:
            metrics[spec.id] = _backtest(spec.build(), warm, test, scenario, cfg)
        for name, p in policies.items():
            metrics[f"rl:{name}"] = _backtest(PolicyStrategy(p), warm, test, scenario, cfg)
        base = metrics[cfg.baseline_id]
        for sid, m in metrics.items():
            runs.append(
                {
                    "scenario": scenario.name,
                    "strategy": sid,
                    "metrics": m.as_dict(),
                    "excess_vs_baseline": None
                    if sid == cfg.baseline_id
                    else str(excess_return(m, base)),
                }
            )
    report = {
        "kind": "simulated",
        "data_label": "SYNTHETIC" if synthetic else "exchange_api",
        "warning": (
            "SYNTHETIC DATA - NOT MARKET EVIDENCE."
            if synthetic
            else "SIMULATED results under assumed execution scenarios; not realised returns."
        ),
        "experiment": cfg.name,
        "train_window": [train[0].as_of.isoformat(), train[-1].as_of.isoformat()],
        "test_window": [test[0].as_of.isoformat(), test[-1].as_of.isoformat()],
        "training_scenario": scenario0.name,
        "reward_config": {k: getattr(cfg.reward, k) for k in cfg.reward.__dataclass_fields__},
        "cem_training_curve": cem_curve,
        "test_env_rewards": env_eval,
        "backtests": runs,
        "notes": [*notes, "RL decisions pass the same constraints and risk gate as baselines."],
    }
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        (output / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    return report


__all__ = ["RLConfig", "run"]
