"""PPO via Stable-Baselines3 (optional ``rl`` extra, pulls in PyTorch)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from fundpilot.rl.env import FundingEnv
from fundpilot.rl.spaces import ObsArray


def available() -> bool:
    try:
        import stable_baselines3  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


@dataclass(slots=True)
class PPOPolicy:
    model: Any
    name: str = "ppo"

    def act(self, obs: ObsArray) -> NDArray[np.int64]:
        action, _ = self.model.predict(obs, deterministic=True)
        return np.asarray(action, dtype=np.int64)

    def save(self, path: Path) -> None:
        self.model.save(str(path))


def train_ppo(
    env: FundingEnv, *, timesteps: int = 20_000, seed: int = 0, **kwargs: Any
) -> PPOPolicy:
    from stable_baselines3 import PPO  # noqa: PLC0415 - optional dependency

    params = {
        "n_steps": 256,
        "batch_size": 64,
        "gamma": 0.99,
        "learning_rate": 3e-4,
        "ent_coef": 0.01,
        "verbose": 0,
        "seed": seed,
        "device": "cpu",
        **kwargs,
    }
    model = PPO("MlpPolicy", env, **params)
    model.learn(total_timesteps=timesteps)
    return PPOPolicy(model)
