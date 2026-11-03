"""Policies for :class:`FundingEnv`: fixed heuristics and a numpy cross-entropy-method agent.

The CEM agent (linear logits per action dimension, argmax at evaluation) is a deliberately
simple, dependency-free RL baseline; PPO via Stable-Baselines3 lives in :mod:`fundpilot.rl.ppo`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from fundpilot.rl.env import FundingEnv
from fundpilot.rl.spaces import ObsArray


class Policy(Protocol):
    name: str

    def act(self, obs: ObsArray) -> NDArray[np.int64]: ...


@dataclass(slots=True)
class ConstantPolicy:
    """Always takes the same action, e.g. ``[premium idx, period idx, fraction idx, cancel]``."""

    action: tuple[int, ...]
    name: str = "constant"

    def act(self, obs: ObsArray) -> NDArray[np.int64]:
        return np.array(self.action, dtype=np.int64)


@dataclass(slots=True)
class LinearPolicy:
    nvec: list[int]
    obs_dim: int
    params: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    name: str = "linear_cem"

    @property
    def size(self) -> int:
        return sum(n * (self.obs_dim + 1) for n in self.nvec)

    def act(self, obs: ObsArray) -> NDArray[np.int64]:
        x = np.append(obs.astype(np.float64), 1.0)
        out, offset = [], 0
        for n in self.nvec:
            w = self.params[offset : offset + n * (self.obs_dim + 1)].reshape(n, self.obs_dim + 1)
            out.append(int(np.argmax(w @ x)))
            offset += n * (self.obs_dim + 1)
        return np.array(out, dtype=np.int64)

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "nvec": self.nvec,
                    "obs_dim": self.obs_dim,
                    "params": self.params.tolist(),
                    "name": self.name,
                }
            )
        )

    @classmethod
    def load(cls, path: Path) -> LinearPolicy:
        d: dict[str, Any] = json.loads(path.read_text())
        return cls(list(d["nvec"]), int(d["obs_dim"]), np.array(d["params"]), d["name"])


def rollout(
    env: FundingEnv, policy: Policy, seed: int, start: int | None = None
) -> tuple[float, dict[str, float]]:
    """Total reward of one episode plus summed reward components."""
    obs, _ = env.reset(seed=seed, options=None if start is None else {"start": start})
    total = 0.0
    comps: dict[str, float] = {}
    while True:
        obs, reward, terminated, truncated, info = env.step(policy.act(obs))
        total += reward
        for k, v in info["components"].items():
            comps[k] = comps.get(k, 0.0) + v
        if terminated or truncated:
            return total, comps


def train_cem(
    env: FundingEnv,
    *,
    iterations: int = 15,
    population: int = 24,
    elite_frac: float = 0.25,
    episodes_per_candidate: int = 2,
    init_std: float = 1.0,
    seed: int = 0,
) -> tuple[LinearPolicy, list[float]]:
    """Cross-entropy method on the training environment. Deterministic given ``seed``."""
    rng = np.random.default_rng(seed)
    obs_dim = int(np.prod(env.observation_space.shape or (0,)))
    policy = LinearPolicy(list(env.grid.nvec), obs_dim)
    mean = np.zeros(policy.size)
    std = np.full(policy.size, init_std)
    n_elite = max(1, int(population * elite_frac))
    history: list[float] = []
    for it in range(iterations):
        seeds = [seed * 10_000 + it * 100 + k for k in range(episodes_per_candidate)]
        cands = rng.normal(mean, std, size=(population, policy.size))
        scores = []
        for c in cands:
            policy.params = c
            scores.append(float(np.mean([rollout(env, policy, s)[0] for s in seeds])))
        order = np.argsort(scores)[::-1][:n_elite]
        elite = cands[order]
        mean, std = elite.mean(axis=0), elite.std(axis=0) + 0.02
        history.append(float(np.mean(np.array(scores)[order])))
    policy.params = mean
    return policy, history


def evaluate_policy(env: FundingEnv, policy: Policy, seeds: Sequence[int]) -> dict[str, float]:
    rewards = [rollout(env, policy, s)[0] for s in seeds]
    return {
        "mean_reward": float(np.mean(rewards)),
        "std_reward": float(np.std(rewards)),
        "episodes": float(len(rewards)),
    }
