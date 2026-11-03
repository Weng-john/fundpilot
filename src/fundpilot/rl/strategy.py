"""Run an RL policy as a regular :class:`~fundpilot.strategies.base.Strategy`.

This lets trained policies be backtested with :class:`FundingSimulator` under exactly the same
execution scenario, constraints and risk gate as the baseline strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fundpilot.core.portfolio import PortfolioState
from fundpilot.ml.features import FeatureBuilder
from fundpilot.rl.policies import Policy
from fundpilot.rl.spaces import ActionGrid, observe
from fundpilot.strategies.base import Decision, MarketState


@dataclass(slots=True)
class PolicyStrategy:
    policy: Policy
    grid: ActionGrid = field(default_factory=ActionGrid)
    _features: FeatureBuilder = field(default_factory=FeatureBuilder, init=False)

    @property
    def name(self) -> str:
        return f"rl_policy({self.policy.name})"

    def observe_only(self, market: MarketState) -> None:
        """Feed a state into the feature history without deciding (warm-up)."""
        if not market.stale:
            self._features.update(market)

    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision:
        feats = self._features.update(market)
        action = self.policy.act(observe(feats, portfolio))
        return self.grid.decode(action, market, portfolio)
