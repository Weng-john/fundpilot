from __future__ import annotations

import dataclasses
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pytest

pytest.importorskip("gymnasium")
from gymnasium.utils.env_checker import check_env

from fundpilot.backtesting.account import SimulationConfig
from fundpilot.backtesting.feed import FeedConfig, MarketFeed
from fundpilot.backtesting.scenarios import NEUTRAL, PRESETS
from fundpilot.backtesting.simulator import FundingSimulator
from fundpilot.backtesting.synthetic import SyntheticConfig, generate
from fundpilot.core.models import DataOrigin
from fundpilot.risk.manager import RiskLimits, RiskManager
from fundpilot.rl import evaluate as rl_eval
from fundpilot.rl.env import FundingEnv, RewardConfig
from fundpilot.rl.policies import ConstantPolicy, rollout, train_cem
from fundpilot.rl.spaces import OBS_NAMES, ActionGrid
from fundpilot.rl.strategy import PolicyStrategy
from fundpilot.strategies.base import MarketState
from tests.conftest import FIXED_NOW

STEP = timedelta(minutes=10)
CAPITAL = Decimal(10000)


@pytest.fixture(scope="module")
def states() -> list[MarketState]:
    t, b, tr = generate(SyntheticConfig(start=FIXED_NOW, days=4, step=STEP, seed=11))
    feed = MarketFeed(
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
    return list(feed)


def _env(states: list[MarketState], **kw: object) -> FundingEnv:
    params: dict[str, object] = dict(
        scenario=NEUTRAL,
        sim_config=SimulationConfig(initial_capital=CAPITAL, decision_interval=STEP * 2),
        episode_decisions=40,
        decision_every=2,
        warmup=144,
    )
    params.update(kw)
    return FundingEnv(states, **params)  # type: ignore[arg-type]


def test_gymnasium_api_compliance(states: list[MarketState]) -> None:
    env = _env(states)
    check_env(env, skip_render_check=True)
    assert env.observation_space.shape == (len(OBS_NAMES),)


def test_reward_equals_scaled_sum_of_components(states: list[MarketState]) -> None:
    env = _env(
        states,
        reward=RewardConfig(
            idle_penalty=0.5,
            lock_penalty_per_day=0.1,
            max_lock_days=1,
            concentration_penalty=0.2,
            action_cost=0.01,
        ),
    )
    obs, _ = env.reset(seed=0)
    for _ in range(20):
        obs, reward, _, truncated, info = env.step(env.action_space.sample())
        assert reward == pytest.approx(1e4 / float(CAPITAL) * sum(info["components"].values()))
        assert env.observation_space.contains(obs)
        if truncated:
            break


@pytest.mark.parametrize("scenario", list(PRESETS.values()))
def test_random_actions_conserve_capital(states: list[MarketState], scenario: object) -> None:
    env = _env(states, scenario=scenario)
    env.reset(seed=3)
    env.action_space.seed(3)
    net_from_rewards = 0.0
    while True:
        _, _, _, truncated, info = env.step(env.action_space.sample())
        net_from_rewards += info["components"]["net_interest"]
        acct = env.account
        assert acct is not None
        snap = acct.log.snapshots[-1]
        assert snap.available >= 0 and snap.offered >= 0 and snap.lent >= 0
        assert abs(snap.equity - (CAPITAL + snap.gross_interest - snap.fees)) < Decimal("1e-12")
        if truncated:
            break
    # rewarded interest is exactly the account's accrued net interest (no free reward)
    assert net_from_rewards == pytest.approx(float(acct.gross - acct.fees), rel=1e-9, abs=1e-12)


def test_absurd_rates_earn_nothing(states: list[MarketState]) -> None:
    env = _env(states, grid=ActionGrid(premiums=(50.0,)))  # 51x FRR: nobody borrows there
    total, comps = rollout(env, ConstantPolicy((0, 0, 3, 0)), seed=1)
    assert comps["net_interest"] == 0.0
    assert total <= 0  # only penalties remain


def test_observation_has_no_lookahead(states: list[MarketState]) -> None:
    env_a = _env(states, random_start=False)
    altered = states[:200] + [dataclasses.replace(s, trades=(), book=None) for s in states[200:]]
    env_b = _env(altered, random_start=False)
    obs_a, _ = env_a.reset(seed=0)
    obs_b, _ = env_b.reset(seed=0)
    assert np.array_equal(obs_a, obs_b)
    action = np.array([1, 0, 3, 0])
    for _ in range(10):  # positions 144..164 < 200: identical history
        obs_a, r_a, *_ = env_a.step(action)
        obs_b, r_b, *_ = env_b.step(action)
        assert np.array_equal(obs_a, obs_b) and r_a == r_b


def test_risk_gate_constrains_rl_actions(states: list[MarketState]) -> None:
    env = _env(states, risk=RiskManager(RiskLimits(max_single_offer=Decimal(500))))
    env.reset(seed=0)
    env.step(np.array([1, 0, 3, 0]))
    acct = env.account
    assert acct is not None
    assert all(o.request.amount <= 500 for o in acct.offers.values())
    assert all(c.amount <= 500 for c in acct.credits.values())


def test_cem_is_deterministic_and_policy_backtests(states: list[MarketState]) -> None:
    env = _env(states, episode_decisions=20)
    p1, c1 = train_cem(env, iterations=2, population=4, episodes_per_candidate=1, seed=5)
    p2, c2 = train_cem(env, iterations=2, population=4, episodes_per_candidate=1, seed=5)
    assert c1 == c2 and np.array_equal(p1.params, p2.params)
    strategy = PolicyStrategy(p1)
    result = FundingSimulator(
        strategy, NEUTRAL, SimulationConfig(initial_capital=CAPITAL), gate=RiskManager().as_gate()
    ).run(states[150:250])
    assert result.steps == 100


def test_rl_experiment_report(states: list[MarketState]) -> None:
    cfg = rl_eval.RLConfig.from_dict(
        {
            "name": "t",
            "start": FIXED_NOW.isoformat(),
            "end": (FIXED_NOW + timedelta(days=4)).isoformat(),
            "step_seconds": 600,
            "decision_every": 2,
            "episode_decisions": 20,
            "warmup_steps": 144,
            "train_fraction": 0.6,
            "scenarios": ["conservative"],
            "cem": {"iterations": 1, "population": 4, "episodes_per_candidate": 1},
            "baselines": [{"id": "frr", "type": "frr"}],
        }
    )
    feed = MarketFeed(
        FeedConfig(
            exchange="bitfinex",
            symbol="fUST",
            start=cfg.start,
            end=cfg.end - STEP,
            step=STEP,
            origins=(DataOrigin.SYNTHETIC,),
        ),
        *generate(SyntheticConfig(start=FIXED_NOW, days=4, step=STEP, seed=11)),
    )
    report = rl_eval.run(cfg, feed, synthetic=True)
    assert report["data_label"] == "SYNTHETIC"
    assert report["train_window"][1] < report["test_window"][0]
    ids = {r["strategy"] for r in report["backtests"]}
    assert {"frr", "rl:cem", "rl:constant_frr_2d_all"} <= ids


def test_ppo_trains_when_available(states: list[MarketState]) -> None:
    pytest.importorskip("stable_baselines3")
    from fundpilot.rl import ppo  # noqa: PLC0415

    env = _env(states, episode_decisions=20)
    policy = ppo.train_ppo(env, timesteps=256, seed=0, n_steps=128, batch_size=32)
    obs, _ = env.reset(seed=0)
    assert env.action_space.contains(policy.act(obs))
