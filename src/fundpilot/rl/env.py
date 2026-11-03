"""Gymnasium environment over the shared :class:`VirtualFundingAccount`.

One environment step = one decision followed by ``decision_every`` market steps.
Decisions pass exchange constraints and (by default) the risk manager, exactly as in
backtests. Reward (in basis points of initial capital):

    reward = scale * (net interest accrued
                      - idle_penalty * idle_capital * FRR * dt          # opportunity cost
                      - lock_penalty * capital-days locked beyond max_lock_days
                      - concentration_penalty * excess period concentration * capital
                      - action_cost * number of offers placed/cancelled)

Components are reported in ``info`` so reward exploitation can be audited.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import localcontext
from typing import Any

import gymnasium as gym
import numpy as np
from numpy.typing import NDArray

from fundpilot.backtesting.account import SimulationConfig, VirtualFundingAccount
from fundpilot.backtesting.scenarios import ExecutionScenario
from fundpilot.ml.features import FeatureBuilder
from fundpilot.risk.manager import RiskManager
from fundpilot.rl.spaces import OBS_NAMES, ActionGrid, ObsArray, observe
from fundpilot.strategies.base import MarketState
from fundpilot.strategies.constraints import FundingConstraints


@dataclass(frozen=True, slots=True)
class RewardConfig:
    idle_penalty: float = 0.5
    lock_penalty_per_day: float = 0.0
    max_lock_days: float = 30.0
    concentration_penalty: float = 0.0
    concentration_limit: float = 0.6
    action_cost: float = 0.0  # currency units per action

    def __post_init__(self) -> None:
        if (
            min(
                self.idle_penalty,
                self.lock_penalty_per_day,
                self.concentration_penalty,
                self.action_cost,
            )
            < 0
        ):
            raise ValueError("penalties must be non-negative")


@dataclass
class FundingEnv(gym.Env[ObsArray, NDArray[np.int64]]):
    states: Sequence[MarketState]
    scenario: ExecutionScenario
    sim_config: SimulationConfig
    episode_decisions: int = 96
    decision_every: int = 5
    warmup: int = 288
    reward: RewardConfig = field(default_factory=RewardConfig)
    risk: RiskManager | None = field(default_factory=RiskManager)
    constraints: FundingConstraints | None = None
    grid: ActionGrid = field(default_factory=ActionGrid)
    random_start: bool = True
    # runtime
    account: VirtualFundingAccount | None = field(default=None, init=False)
    _features: FeatureBuilder = field(default_factory=FeatureBuilder, init=False)
    _pos: int = field(default=0, init=False)
    _t: int = field(default=0, init=False)
    _last_feats: list[float] = field(default_factory=list, init=False)
    _rng: np.random.Generator = field(default_factory=np.random.default_rng, init=False)

    def __post_init__(self) -> None:
        super().__init__()
        self.observation_space = gym.spaces.Box(-10.0, 10.0, (len(OBS_NAMES),), np.float32)
        self.action_space = gym.spaces.MultiDiscrete(self.grid.nvec)
        need = self.warmup + self.episode_decisions * self.decision_every + 1
        if len(self.states) < need:
            raise ValueError(f"need at least {need} market states, got {len(self.states)}")

    # -- gym API -------------------------------------------------------------------
    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[ObsArray, dict[str, Any]]:
        super().reset(seed=seed)
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        span = self.episode_decisions * self.decision_every
        last_start = len(self.states) - span - 1
        start = (
            int(self._rng.integers(self.warmup, last_start + 1))
            if self.random_start
            else self.warmup
        )
        if options and "start" in options:
            start = int(options["start"])
        self._features = FeatureBuilder()
        for s in self.states[start - self.warmup : start]:
            if not s.stale:
                self._features.update(s)
        cfg = self.sim_config
        if seed is not None:
            cfg = replace(cfg, seed=seed)
        self.account = VirtualFundingAccount(self.scenario, cfg, self.constraints, "rl")
        self._pos, self._t = start, 0
        market = self.states[start]
        with localcontext() as ctx:
            ctx.prec = 40
            self.account.advance(market)
            self.account.snapshot()
        self._last_feats = (
            self._features.update(market)
            if not market.stale
            else (self._last_feats or [float("nan")] * len(FeatureBuilder.names()))
        )
        return self._obs(), {"start_index": start, "as_of": market.as_of.isoformat()}

    def step(self, action: NDArray[np.int64]) -> tuple[ObsArray, float, bool, bool, dict[str, Any]]:
        acct = self.account
        if acct is None:
            raise RuntimeError("call reset() first")
        with localcontext() as ctx:
            ctx.prec = 40
            market = self.states[self._pos]
            portfolio = acct.portfolio(market.as_of)
            n_actions = 0
            if not market.stale:
                decision = self.grid.decode(action, market, portfolio)
                if self.risk is not None:
                    decision = self.risk.as_gate()(decision, portfolio, market)
                placed_before = acct.log.offers_placed
                cancelled_before = acct.log.offers_cancelled
                acct.apply(decision)
                acct.activate()
                n_actions = (
                    acct.log.offers_placed
                    - placed_before
                    + acct.log.offers_cancelled
                    - cancelled_before
                )
            net_before = acct.gross - acct.fees
            idle_cost = lock_cost = conc_cost = 0.0
            fee = float(acct.constraints.lender_fee)
            for _ in range(self.decision_every):
                prev = self.states[self._pos]
                self._pos += 1
                nxt = self.states[self._pos]
                dt_days = (nxt.as_of - prev.as_of).total_seconds() / 86_400
                frr = float(prev.frr) if prev.frr else 0.0
                idle_cost += float(acct.available) * frr * (1 - fee) * dt_days
                acct.advance(nxt)
                acct.snapshot()
                if not nxt.stale:
                    self._last_feats = self._features.update(nxt)
                pf = acct.portfolio(nxt.as_of)
                limit = timedelta(days=self.reward.max_lock_days)
                lock_cost += (
                    sum(float(c.amount) for c in pf.credits if c.remaining(nxt.as_of) > limit)
                    * dt_days
                )
                total = float(pf.total) or 1.0
                shares = [float(pf.allocated_by_period(p)) / total for p in self.grid.periods]
                conc_cost += (
                    max(0.0, max(shares) - self.reward.concentration_limit) * total * dt_days
                )
            net_gain = float(acct.gross - acct.fees - net_before)
        r = self.reward
        components = {
            "net_interest": net_gain,
            "idle_opportunity_cost": -r.idle_penalty * idle_cost,
            "lock_penalty": -r.lock_penalty_per_day * lock_cost,
            "concentration_penalty": -r.concentration_penalty * conc_cost,
            "action_cost": -r.action_cost * n_actions,
        }
        scale = 1e4 / float(self.sim_config.initial_capital)
        reward = scale * sum(components.values())
        self._t += 1
        truncated = self._t >= self.episode_decisions or self._pos >= len(self.states) - 1
        info = {
            "components": components,
            "as_of": self.states[self._pos].as_of.isoformat(),
            "equity": float(acct.log.snapshots[-1].equity),
        }
        return self._obs(), float(reward), False, truncated, info

    def _obs(self) -> ObsArray:
        assert self.account is not None  # noqa: S101
        market = self.states[self._pos]
        return observe(self._last_feats, self.account.portfolio(market.as_of))
