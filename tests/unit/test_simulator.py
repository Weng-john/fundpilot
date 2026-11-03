from __future__ import annotations

import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest

from fundpilot.backtesting.scenarios import NEUTRAL, OPTIMISTIC, PRESETS, ExecutionScenario
from fundpilot.backtesting.simulator import FundingSimulator, SimulationConfig
from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.strategies import AdaptiveFundingLadder, FixedRateStrategy, FRRStrategy
from fundpilot.strategies.base import Decision, MarketState, OfferRequest
from tests.conftest import FIXED_NOW
from tests.helpers import level, series, state

D = Decimal
EPS = D("1e-20")
NO_REPAY = dataclasses.replace(OPTIMISTIC, early_repay_hazard_per_day=D(0), latency_steps=0)


def _cfg(capital: str = "1000", **kw: object) -> SimulationConfig:
    return SimulationConfig(initial_capital=D(capital), **kw)  # type: ignore[arg-type]


class OneShot:
    """Places a single offer once, then does nothing."""

    def __init__(self, req: OfferRequest) -> None:
        self.req = req
        self.done = False

    @property
    def name(self) -> str:
        return "one_shot"

    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision:
        if self.done:
            return Decision()
        self.done = True
        return Decision((self.req,))


def _fixed(amount: str = "1000", rate: str = "0.0002", period: int = 2) -> OneShot:
    return OneShot(OfferRequest(RateType.FIXED, D(rate), period, D(amount)))


def test_bid_cross_fill_and_exact_interest_to_maturity() -> None:
    # Bid at our rate absorbs the whole offer immediately; credit runs 2 days.
    states = series(2 * 24 + 1, timedelta(hours=1), levels=[level("bid", "0.0002", "5000")])
    sim = FundingSimulator(_fixed(), NO_REPAY, _cfg(payout_interval=timedelta(days=10)))
    result = sim.run(states)
    last = result.snapshots[-1]
    assert result.fills[0].source == "bid_cross"
    assert last.gross_interest.quantize(EPS) == D("0.4")  # 1000 * 0.0002 * 2 days
    assert last.fees.quantize(EPS) == D("0.06")  # 15 %
    assert last.lent == 0 and last.available == D(1000)  # principal returned at maturity
    assert last.pending_interest.quantize(EPS) == D("0.34")
    assert result.closes[0].reason == "matured"


def test_capital_locked_until_maturity() -> None:
    states = series(30, timedelta(hours=1), levels=[level("bid", "0.0002", "5000")])
    result = FundingSimulator(_fixed(), NO_REPAY, _cfg()).run(states)
    # only paid-out interest becomes available; principal stays locked
    assert all(s.available == s.paid_interest for s in result.snapshots)
    assert all(s.lent == D(1000) for s in result.snapshots)


def test_unfilled_offer_without_trades_or_bids() -> None:
    result = FundingSimulator(_fixed(), NEUTRAL, _cfg()).run(series(100))
    assert result.fills == []
    assert result.snapshots[-1].offered == D(1000)
    assert result.snapshots[-1].gross_interest == 0


def test_partial_fills_and_queue_ahead() -> None:
    scenario = dataclasses.replace(
        NO_REPAY,
        participation=D(1),
        same_rate_queue_fraction=D(1),
        period_match=PRESETS["neutral"].period_match,
    )
    levels = [level("ask", "0.0001", "300"), level("ask", "0.0002", "200")]  # 500 ahead
    states = series(5, levels=levels, trades_per_step=[("0.0003", "400", 2)])
    result = FundingSimulator(_fixed(), scenario, _cfg()).run(states)
    amounts = [f.amount for f in result.fills]
    # step1: 400 traded, all consumed by the queue; step2: 100 queue + 300 ours; ...
    assert amounts == [D(300), D(400), D(300)]
    assert len({f.credit_id for f in result.fills}) == 3
    assert result.snapshots[-1].lent == D(1000)


def test_trades_below_our_rate_do_not_fill() -> None:
    states = series(20, trades_per_step=[("0.0001", "100000", 2)])
    result = FundingSimulator(_fixed(rate="0.0002"), NO_REPAY, _cfg()).run(states)
    assert result.fills == []


def test_strict_rate_and_period_matching() -> None:
    conservative = dataclasses.replace(PRESETS["conservative"], early_repay_hazard_per_day=D(0))
    equal_rate = series(20, trades_per_step=[("0.0002", "100000", 2)])
    assert FundingSimulator(_fixed(), conservative, _cfg()).run(equal_rate).fills == []
    other_period = series(20, trades_per_step=[("0.0003", "100000", 30)])
    neutral = dataclasses.replace(NEUTRAL, early_repay_hazard_per_day=D(0))
    assert FundingSimulator(_fixed(), neutral, _cfg()).run(other_period).fills == []


def test_latency_ignores_trades_before_activation() -> None:
    scenario = dataclasses.replace(NO_REPAY, latency_steps=3, participation=D(1))
    states = series(3, trades_per_step=[("0.0003", "100000", 2)])
    result = FundingSimulator(_fixed(), scenario, _cfg()).run(states)
    assert result.fills == []


def test_early_repayment_charges_minimum_interest() -> None:
    scenario = dataclasses.replace(NO_REPAY, early_repay_hazard_per_day=D(1_000_000))
    states = series(3, timedelta(minutes=1), levels=[level("bid", "0.0002", "5000")])
    result = FundingSimulator(_fixed(), scenario, _cfg()).run(states)
    close = result.closes[0]
    assert close.reason == "early_repayment"
    # at least one hour of interest: 1000 * 0.0002 / 24
    assert close.gross_interest.quantize(EPS) == (D(1000) * D("0.0002") / 24).quantize(EPS)
    assert result.snapshots[-1].available + result.snapshots[-1].offered >= D(1000) - D(1)


def test_payout_reinvests_interest() -> None:
    states = series(4 * 24 + 1, timedelta(hours=1), levels=[level("bid", "0.0002", "1e9")])
    strategy = FixedRateStrategy(D("0.0002"), period_days=2)
    result = FundingSimulator(strategy, NO_REPAY, _cfg("10000")).run(states)
    last = result.snapshots[-1]
    assert last.paid_interest > 0
    # paid interest was lent out again, so total lent exceeds the initial capital
    assert last.lent > D(10000)


def test_frr_credit_tracks_current_frr() -> None:
    first = series(25, timedelta(hours=1), frr="0.0002", levels=[level("bid", "0.001", "1e9")])
    second = series(
        24, timedelta(hours=1), start=first[-1].as_of + timedelta(hours=1), frr="0.0004"
    )
    strategy = OneShot(OfferRequest(RateType.FRR, None, 2, D(1000)))
    result = FundingSimulator(strategy, NO_REPAY, _cfg(payout_interval=timedelta(days=9))).run(
        first + second
    )
    # ~1 day at 0.0002 then ~1 day at 0.0004 on 1000 -> ~0.6 gross (vs 0.4 if rate frozen)
    assert D("0.55") < result.snapshots[-1].gross_interest <= D("0.6")


def test_stale_steps_block_fills_and_decisions() -> None:
    states = [
        dataclasses.replace(s, stale=True)
        for s in series(10, trades_per_step=[("0.001", "1e6", 2)])
    ]
    result = FundingSimulator(FRRStrategy(), NO_REPAY, _cfg()).run(states)
    assert result.offers_placed == 0 and result.stale_steps == 10


def test_non_monotonic_feed_rejected() -> None:
    s = state(FIXED_NOW)
    with pytest.raises(ValueError):
        FundingSimulator(FRRStrategy(), NEUTRAL, _cfg()).run([s, s])


@pytest.mark.parametrize("scenario", list(PRESETS.values()))
def test_deterministic_with_seed(scenario: ExecutionScenario) -> None:
    states = series(
        300,
        levels=[level("ask", "0.0002", "1000"), level("bid", "0.00019", "500")],
        trades_per_step=[("0.00025", "300", 2), ("0.0003", "200", 30)],
    )
    runs = [
        FundingSimulator(AdaptiveFundingLadder(), scenario, _cfg("50000", seed=3)).run(states)
        for _ in range(2)
    ]
    assert runs[0].snapshots == runs[1].snapshots
    assert runs[0].fills == runs[1].fills


def test_no_lookahead_future_changes_do_not_affect_past() -> None:
    base = series(
        200, trades_per_step=[("0.00025", "-300", 2)], levels=[level("ask", "0.0002", "100")]
    )
    altered = base[:100] + [dataclasses.replace(s, trades=(), book=None) for s in base[100:]]
    a = FundingSimulator(AdaptiveFundingLadder(), NEUTRAL, _cfg("5000000", seed=1)).run(base)
    b = FundingSimulator(AdaptiveFundingLadder(), NEUTRAL, _cfg("5000000", seed=1)).run(altered)
    assert a.snapshots[:100] == b.snapshots[:100]
    assert a.snapshots[100:] != b.snapshots[100:]


def test_only_funding_taken_fills_resting_offers() -> None:
    """Docs: funding trade AMOUNT < 0 = funding taken by the taker (fills resting offers)."""
    neutral = dataclasses.replace(
        NEUTRAL,
        early_repay_hazard_per_day=D(0),
        latency_steps=0,
        participation=D(1),
        same_rate_queue_fraction=D(0),
    )
    provided = series(10, trades_per_step=[("0.0003", "500", 2)])  # lender takers only
    taken = series(10, trades_per_step=[("0.0003", "-500", 2)])
    assert FundingSimulator(_fixed(), neutral, _cfg()).run(provided).fills == []
    assert FundingSimulator(_fixed(), neutral, _cfg()).run(taken).fills
    optimistic = dataclasses.replace(neutral, count_lender_taker_volume=True)
    assert FundingSimulator(_fixed(), optimistic, _cfg()).run(provided).fills
