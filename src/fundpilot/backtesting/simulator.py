"""Backtest driver: strategy -> (optional risk gate) -> virtual account, per market step.

See :mod:`fundpilot.backtesting.account` for the step order and execution model.
Stale market steps are treated as a disruption: no fills and no decisions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import localcontext

from fundpilot.backtesting.account import (
    CreditClose,
    FillEvent,
    SimulationConfig,
    SimulationResult,
    Snapshot,
    VirtualFundingAccount,
)
from fundpilot.backtesting.scenarios import ExecutionScenario
from fundpilot.core.portfolio import PortfolioState
from fundpilot.strategies.base import Decision, MarketState, Strategy
from fundpilot.strategies.constraints import FundingConstraints

__all__ = [
    "CreditClose",
    "DecisionGate",
    "FillEvent",
    "FundingSimulator",
    "SimulationConfig",
    "SimulationResult",
    "Snapshot",
]

DecisionGate = Callable[[Decision, PortfolioState, MarketState], Decision]


class FundingSimulator:
    def __init__(
        self,
        strategy: Strategy,
        scenario: ExecutionScenario,
        config: SimulationConfig,
        constraints: FundingConstraints | None = None,
        gate: DecisionGate | None = None,
    ) -> None:
        self.strategy = strategy
        self.scenario = scenario
        self.config = config
        self.constraints = constraints
        self.gate = gate

    def run(self, feed: Iterable[MarketState]) -> SimulationResult:
        with localcontext() as ctx:
            ctx.prec = 40
            account = VirtualFundingAccount(
                self.scenario, self.config, self.constraints, self.strategy.name
            )
            last_decision: datetime | None = None
            for market in feed:
                account.advance(market)
                due = last_decision is None or (
                    market.as_of - last_decision >= self.config.decision_interval
                )
                if not market.stale and due:
                    state = account.portfolio(market.as_of)
                    decision = self.strategy.decide(market, state)
                    if self.gate is not None:
                        decision = self.gate(decision, state, market)
                    account.apply(decision)
                    account.activate()
                    last_decision = market.as_of
                account.snapshot()
            return account.log
