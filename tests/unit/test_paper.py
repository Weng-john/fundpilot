from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.backtesting.account import (
    SimulationConfig,
    VirtualFundingAccount,
    account_from_state,
    account_to_state,
)
from fundpilot.backtesting.scenarios import NEUTRAL
from fundpilot.core.models import FundingBookSnapshot, FundingTicker, FundingTrade
from fundpilot.data.db import session_scope
from fundpilot.data.models import (
    ExecutionAuditRow,
    FundingCreditEventRow,
    FundingOfferEventRow,
    FundingWalletSnapshotRow,
)
from fundpilot.execution.controls import Controls
from fundpilot.paper.engine import PaperConfig, PaperTradingEngine
from fundpilot.paper.source import HealthMonitor, LiveMarketSource
from fundpilot.strategies import AdaptiveFundingLadder
from tests.conftest import FIXED_NOW
from tests.helpers import book, level, series, ticker, trade

D = Decimal

RAW = {
    "account": "paper-test",
    "scenario": "optimistic",
    "poll_interval_seconds": 60,
    "simulation": {"initial_capital": "10000", "decision_interval_seconds": 60},
    "strategy": {"id": "frr", "type": "frr"},
    "risk": {"max_single_offer": "4000"},
}


class ScriptedAdapter:
    """Serves synthetic ticker/book/trades for whatever time the clock says."""

    exchange = "bitfinex"

    def __init__(self, clock: Callable[[], datetime], fail_at: set[int] | None = None) -> None:
        self.clock = clock
        self.calls = 0
        self.fail_at = fail_at or set()

    def _tick(self) -> None:
        if self.calls in self.fail_at:
            self.calls += 1
            raise ConnectionError("network down")

    def get_funding_ticker(self, symbol: str) -> FundingTicker:
        self._tick()
        self.calls += 1
        return ticker(self.clock(), "0.0002")

    def get_funding_book(
        self, symbol: str, precision: str = "P0", length: int = 100
    ) -> FundingBookSnapshot:
        return book(
            self.clock(), [level("bid", "0.0003", "100000"), level("ask", "0.0002", "1000")]
        )

    def get_funding_trades(
        self, symbol: str, *, limit: int | None = None, **_: object
    ) -> list[FundingTrade]:
        now = self.clock()
        return [
            trade(now - timedelta(seconds=5), "0.0003", "2000"),
            trade(now + timedelta(seconds=5), "0.0003", "9e9"),
        ]  # future: must be ignored


class Clock:
    def __init__(self) -> None:
        self.now = FIXED_NOW

    def __call__(self) -> datetime:
        return self.now

    def tick(self, minutes: int = 1) -> None:
        self.now += timedelta(minutes=minutes)


def _engine(
    sessions: sessionmaker[Session], clock: Clock, adapter: ScriptedAdapter | None = None
) -> tuple[PaperTradingEngine, LiveMarketSource]:
    adapter = adapter or ScriptedAdapter(clock)
    source = LiveMarketSource(adapter, "fUST", clock=clock)  # type: ignore[arg-type]
    controls = Controls(sessions)
    monitor = HealthMonitor(controls, source, platform_check=lambda: True)
    return PaperTradingEngine(
        PaperConfig.from_dict(RAW), source, monitor, sessions, controls=controls
    ), source


def _count(sessions: sessionmaker[Session], model: type) -> int:
    with session_scope(sessions) as s:
        return int(s.scalar(select(func.count()).select_from(model)) or 0)


def test_paper_loop_places_fills_and_persists(sessions: sessionmaker[Session]) -> None:
    clock = Clock()
    engine, _ = _engine(sessions, clock)
    for _ in range(5):
        engine.step()
        clock.tick()
    acct = engine.account
    assert acct.log.fills, "offers should fill against bids/trades in the optimistic scenario"
    assert all(o.request.amount <= D(4000) for o in acct.offers.values())  # risk cap applied
    assert _count(sessions, FundingOfferEventRow) > 0
    assert _count(sessions, FundingCreditEventRow) > 0
    assert _count(sessions, FundingWalletSnapshotRow) == 5
    with session_scope(sessions) as s:
        modes = set(s.scalars(select(ExecutionAuditRow.mode)))
    assert modes == {"paper"}


def test_future_trades_are_not_admitted(sessions: sessionmaker[Session]) -> None:
    clock = Clock()
    _, source = _engine(sessions, clock)
    market = source()
    assert all(t.executed_at <= market.as_of for t in market.trades)


def test_restart_resumes_state(sessions: sessionmaker[Session]) -> None:
    clock = Clock()
    engine, _ = _engine(sessions, clock)
    for _ in range(3):
        engine.step()
        clock.tick()
    before = account_to_state(engine.account)
    resumed, _ = _engine(sessions, clock)
    assert account_to_state(resumed.account) == before
    resumed.step()  # continues from persisted state without errors


def test_api_failures_mark_stale_and_block(sessions: sessionmaker[Session]) -> None:
    clock = Clock()
    adapter = ScriptedAdapter(clock, fail_at={1, 2, 3, 4, 5, 6})
    engine, source = _engine(sessions, clock, adapter)
    engine.step()  # success
    for _ in range(4):
        clock.tick(3)
        report = engine.step()
    assert source.consecutive_failures >= 3
    assert report is not None and report.verdict.new_offers_blocked
    assert not engine.account.offers  # unfilled offers cancelled while blocked


def test_emergency_stop_cancels_unfilled_keeps_credits(sessions: sessionmaker[Session]) -> None:
    clock = Clock()
    engine, _ = _engine(sessions, clock)
    engine.step()
    clock.tick()
    lent_before = sum(c.amount for c in engine.account.credits.values())
    Controls(sessions).emergency_stop("drill", by="test", now=clock())
    report = engine.step()
    assert report is not None and report.verdict.new_offers_blocked
    assert not engine.account.offers
    assert sum(c.amount for c in engine.account.credits.values()) >= lent_before > 0


def test_account_state_roundtrip_is_exact() -> None:
    """Interrupting and restoring the account must not change simulated results."""
    states = series(
        400,
        levels=[level("ask", "0.0002", "500"), level("bid", "0.00019", "300")],
        trades_per_step=[("0.00025", "300", 2), ("0.0003", "200", 30)],
    )
    cfg = SimulationConfig(initial_capital=D(20000), seed=4, decision_interval=timedelta(minutes=1))

    def drive(acct: VirtualFundingAccount, strat: AdaptiveFundingLadder, chunk: list) -> None:  # type: ignore[type-arg]
        for m in chunk:
            acct.advance(m)
            if not m.stale:
                acct.apply(strat.decide(m, acct.portfolio(m.as_of)))
                acct.activate()
            acct.snapshot()

    straight = VirtualFundingAccount(NEUTRAL, cfg)
    s1 = AdaptiveFundingLadder()
    drive(straight, s1, states)

    first = VirtualFundingAccount(NEUTRAL, cfg)
    s2 = AdaptiveFundingLadder()
    drive(first, s2, states[:200])
    restored = account_from_state(account_to_state(first), NEUTRAL, cfg)
    drive(restored, s2, states[200:])
    assert account_to_state(restored) == account_to_state(straight)
