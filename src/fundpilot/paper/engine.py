"""Paper trading engine.

Each step: market state -> health -> virtual account advance (fills, accrual, maturities)
-> strategy decision -> ExecutionGateway (risk manager, approvals, idempotency, audit)
-> paper backend (virtual account) -> snapshot -> persist events and account state.

Paper results are SIMULATED fills against live public data; they are stored with
``mode='paper'`` and must never be presented as live performance.
"""

from __future__ import annotations

import logging
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from fundpilot.backtesting.account import (
    SimulationConfig,
    VirtualFundingAccount,
    account_from_state,
    account_to_state,
)
from fundpilot.backtesting.experiment import StrategySpec
from fundpilot.backtesting.scenarios import PRESETS, ExecutionScenario
from fundpilot.config import ExecutionMode
from fundpilot.data.db import session_scope
from fundpilot.data.models import (
    FundingCreditEventRow,
    FundingInterestPaymentRow,
    FundingOfferEventRow,
    FundingWalletSnapshotRow,
    PaperAccountStateRow,
)
from fundpilot.data.repository import insert_ignore
from fundpilot.execution.controls import Controls
from fundpilot.execution.gateway import ExecutionGateway, ExecutionReport
from fundpilot.paper.source import HealthMonitor
from fundpilot.risk.manager import RiskLimits, RiskManager
from fundpilot.strategies.base import Decision, MarketState, OfferRequest, Strategy

logger = logging.getLogger(__name__)
MODE = "paper"


class PaperBackend:
    """Execution backend that acts on the virtual account."""

    def __init__(self, account: VirtualFundingAccount) -> None:
        self.account = account

    def place(self, request: OfferRequest) -> str:
        return self.account.place_offer(request)

    def cancel(self, offer_id: str) -> None:
        self.account.cancel_offer(offer_id)


@dataclass(frozen=True, slots=True)
class PaperConfig:
    account: str
    symbol: str
    exchange: str
    strategy: StrategySpec
    scenario: ExecutionScenario
    simulation: SimulationConfig
    limits: RiskLimits
    poll_interval: timedelta
    stablecoin_pair: str | None

    @classmethod
    def from_toml(cls, path: Path) -> PaperConfig:
        return cls.from_dict(tomllib.loads(path.read_text()))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PaperConfig:
        s = raw["strategy"]
        sim = raw.get("simulation", {})
        risk = raw.get("risk", {})

        def dec(key: str) -> Decimal | None:
            return None if risk.get(key) is None else Decimal(str(risk[key]))

        limits = RiskLimits(
            **{
                k: v
                for k, v in {
                    "max_allocation_fraction": dec("max_allocation_fraction"),
                    "max_period_days": risk.get("max_period_days"),
                    "max_single_offer": dec("max_single_offer"),
                    "min_daily_rate": dec("min_daily_rate"),
                    "min_liquidity_fraction": dec("min_liquidity_fraction"),
                    "max_period_concentration": dec("max_period_concentration"),
                    "depeg_threshold": dec("depeg_threshold"),
                    "approval_threshold": dec("approval_threshold"),
                }.items()
                if v is not None
            }
        )
        return cls(
            account=raw["account"],
            symbol=raw.get("symbol", "fUST"),
            exchange=raw.get("exchange", "bitfinex"),
            strategy=StrategySpec(
                s["id"], s["type"], {k: v for k, v in s.items() if k not in {"id", "type"}}
            ),
            scenario=PRESETS[raw.get("scenario", "conservative")],
            simulation=SimulationConfig(
                initial_capital=Decimal(str(sim.get("initial_capital", "10000"))),
                currency=sim.get("currency", "UST"),
                decision_interval=timedelta(seconds=int(sim.get("decision_interval_seconds", 300))),
                seed=int(raw.get("seed", 0)),
            ),
            limits=limits,
            poll_interval=timedelta(seconds=int(raw.get("poll_interval_seconds", 60))),
            stablecoin_pair=raw.get("stablecoin_pair"),
        )


class PaperTradingEngine:
    def __init__(
        self,
        config: PaperConfig,
        source: Callable[[], MarketState],
        health: HealthMonitor,
        sessions: sessionmaker[Session],
        *,
        strategy: Strategy | None = None,
        controls: Controls | None = None,
    ) -> None:
        self.config = config
        self.source = source
        self.health = health
        self.sessions = sessions
        self.strategy = strategy or config.strategy.build()
        self.controls = controls or Controls(sessions)
        self.account = self._load_account()
        self.gateway = ExecutionGateway(
            mode=ExecutionMode.PAPER,
            account=config.account,
            risk=RiskManager(config.limits),
            controls=self.controls,
            sessions=sessions,
            backend=PaperBackend(self.account),
        )
        self._last_decision: datetime | None = None
        self._cursor = {"fills": 0, "closes": 0, "offers": 0, "payouts": 0}

    # -- state -----------------------------------------------------------------
    def _load_account(self) -> VirtualFundingAccount:
        with session_scope(self.sessions) as s:
            row = s.get(PaperAccountStateRow, self.config.account)
            state = None if row is None else dict(row.state)
        if state is None:
            logger.info("starting new paper account %s", self.config.account)
            return VirtualFundingAccount(
                self.config.scenario, self.config.simulation, strategy_name=self.strategy.name
            )
        logger.info("resuming paper account %s", self.config.account)
        return account_from_state(
            state, self.config.scenario, self.config.simulation, strategy_name=self.strategy.name
        )

    def _save_account(self, now: datetime) -> None:
        state = account_to_state(self.account)
        with session_scope(self.sessions) as s:
            row = s.get(PaperAccountStateRow, self.config.account)
            if row is None:
                s.add(
                    PaperAccountStateRow(account=self.config.account, updated_at=now, state=state)
                )
            else:
                row.state, row.updated_at = state, now

    # -- loop ------------------------------------------------------------------
    def step(self) -> ExecutionReport | None:
        market = self.source()
        health = self.health.status(market.as_of)
        self.account.advance(market)
        report: ExecutionReport | None = None
        due = self._last_decision is None or (
            market.as_of - self._last_decision >= self.config.simulation.decision_interval
        )
        if due:
            portfolio = self.account.portfolio(market.as_of)
            decision = Decision() if market.stale else self.strategy.decide(market, portfolio)
            report = self.gateway.execute(
                decision,
                portfolio,
                market,
                health,
                decision_id=f"{self.config.account}:{market.as_of}",
            )
            self.account.activate()
            self._last_decision = market.as_of
        snap = self.account.snapshot()
        self._persist(market.as_of, snap.equity, snap.available, snap.pending_interest)
        self._save_account(market.as_of)
        return report

    def run(
        self, iterations: int | None = None, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        count = 0
        while iterations is None or count < iterations:
            started = time.monotonic()
            self.step()
            count += 1
            if iterations is not None and count >= iterations:
                break
            elapsed = time.monotonic() - started
            sleep(max(0.0, self.config.poll_interval.total_seconds() - elapsed))

    def _persist(
        self, now: datetime, equity: Decimal, available: Decimal, pending: Decimal
    ) -> None:
        acct, cfg, log = self.config.account, self.config, self.account.log
        base = {"account": acct, "mode": MODE, "exchange": cfg.exchange}
        offers = [
            {
                **base,
                "offer_id": e.offer_id,
                "client_ref": None,
                "symbol": cfg.symbol,
                "event": e.event,
                "event_at": e.at,
                "amount": e.amount,
                "amount_orig": None,
                "rate": e.rate,
                "period_days": e.period_days,
                "status_raw": e.rate_type.value,
            }
            for e in log.offer_events[self._cursor["offers"] :]
        ]
        credits = [
            {
                **base,
                "credit_id": f.credit_id,
                "offer_id": f.offer_id,
                "symbol": cfg.symbol,
                "event": "opened",
                "event_at": f.at,
                "amount": f.amount,
                "rate": f.rate,
                "rate_type": f.rate_type.value,
                "period_days": f.period_days,
                "opened_at": f.at,
            }
            for f in log.fills[self._cursor["fills"] :]
        ] + [
            {
                **base,
                "credit_id": c.credit_id,
                "offer_id": None,
                "symbol": cfg.symbol,
                "event": "closed",
                "event_at": c.at,
                "amount": c.amount,
                "rate": None,
                "rate_type": c.reason,
                "period_days": 0,
                "opened_at": None,
            }
            for c in log.closes[self._cursor["closes"] :]
        ]
        payouts = [
            {
                **base,
                "currency": cfg.simulation.currency,
                "external_id": f"paper-payout-{at.isoformat()}",
                "paid_at": at,
                "net_amount": amt,
                "description": "simulated interest payout (paper)",
            }
            for at, amt in log.payouts[self._cursor["payouts"] :]
        ]
        with session_scope(self.sessions) as s:
            insert_ignore(s, FundingOfferEventRow, offers)
            insert_ignore(s, FundingCreditEventRow, credits)
            insert_ignore(s, FundingInterestPaymentRow, payouts)
            insert_ignore(
                s,
                FundingWalletSnapshotRow,
                [
                    {
                        **base,
                        "currency": cfg.simulation.currency,
                        "captured_at": now,
                        "balance": equity,
                        "available": available,
                        "unsettled_interest": pending,
                    }
                ],
            )
        self._cursor = {
            "fills": len(log.fills),
            "closes": len(log.closes),
            "offers": len(log.offer_events),
            "payouts": len(log.payouts),
        }
