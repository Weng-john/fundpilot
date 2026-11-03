from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from fundpilot.core.portfolio import CreditView, OfferView, PortfolioState, RateType
from fundpilot.risk.manager import (
    CREDITS_NOT_RECALLABLE,
    HealthStatus,
    RiskLimits,
    RiskManager,
)
from fundpilot.strategies.base import Decision, OfferRequest
from tests.conftest import FIXED_NOW
from tests.helpers import state

D = Decimal
OK = HealthStatus(FIXED_NOW, platform_operational=True, last_market_data_at=FIXED_NOW)


def _req(amount: str, period: int = 2, rate: str | None = "0.0002") -> OfferRequest:
    return OfferRequest(
        RateType.FIXED if rate else RateType.FRR, D(rate) if rate else None, period, D(amount)
    )


def _pf(available: str = "10000", offers=(), credits=()) -> PortfolioState:  # type: ignore[no-untyped-def]
    return PortfolioState(FIXED_NOW, "UST", D(available), tuple(offers), tuple(credits))


def _offer(oid: str, amount: str, period: int = 2) -> OfferView:
    return OfferView(
        oid, "fUST", RateType.FIXED, D("0.0002"), period, D(amount), D(amount), FIXED_NOW
    )


def _credit(amount: str, period: int = 2) -> CreditView:
    return CreditView(
        "c",
        "fUST",
        RateType.FIXED,
        D("0.0002"),
        period,
        D(amount),
        FIXED_NOW,
        FIXED_NOW + timedelta(days=period),
    )


def test_passes_clean_decision() -> None:
    v = RiskManager().evaluate(Decision((_req("1000"),)), _pf(), state(FIXED_NOW), OK)
    assert v.decision.place[0].amount == D(1000) and not v.rejected and not v.new_offers_blocked


def test_static_limits() -> None:
    rm = RiskManager(RiskLimits(max_period_days=30, min_daily_rate=D("0.0001")))
    v = rm.evaluate(
        Decision((_req("500", 60), _req("500", rate="0.00005"), _req("500", rate=None))),
        _pf(),
        state(FIXED_NOW, frr="0.00009"),
        OK,
    )
    assert v.decision.place == ()
    assert len(v.rejected) == 3


def test_caps_shrink_offers() -> None:
    rm = RiskManager(
        RiskLimits(
            max_single_offer=D(3000),
            max_allocation_fraction=D("0.5"),
            min_liquidity_fraction=D("0.1"),
        )
    )
    v = rm.evaluate(Decision((_req("4000"), _req("4000"))), _pf("10000"), state(FIXED_NOW), OK)
    assert [p.amount for p in v.decision.place] == [D(3000), D(2000)]  # 50 % allocation cap
    assert any("allocation" in n for n in v.adjustments)


def test_liquidity_and_concentration() -> None:
    rm = RiskManager(RiskLimits(min_liquidity_fraction=D("0.2"), max_period_concentration=D("0.5")))
    pf = _pf("5000", credits=[_credit("4000", 2)], offers=[_offer("o1", "1000", 30)])
    v = rm.evaluate(Decision((_req("3000", 2), _req("5000", 30))), pf, state(FIXED_NOW), OK)
    # total 10000: 2d already 4000 -> 1000 room; liquidity keeps 2000 -> 30d gets 2000
    assert [(p.period_days, p.amount) for p in v.decision.place] == [(2, D(1000)), (30, D(2000))]


def test_approval_threshold_holds_large_offers() -> None:
    v = RiskManager(RiskLimits(approval_threshold=D(2000))).evaluate(
        Decision((_req("5000"), _req("1000"))), _pf(), state(FIXED_NOW), OK
    )
    assert [p.amount for p in v.needs_approval] == [D(5000)]
    assert [p.amount for p in v.decision.place] == [D(1000)]


@pytest.mark.parametrize(
    ("health", "code"),
    [
        (
            HealthStatus(
                FIXED_NOW,
                emergency_stop=True,
                emergency_reason="test",
                last_market_data_at=FIXED_NOW,
            ),
            "emergency_stop",
        ),
        (
            HealthStatus(FIXED_NOW, platform_operational=False, last_market_data_at=FIXED_NOW),
            "exchange_maintenance",
        ),
        (
            HealthStatus(FIXED_NOW, consecutive_api_failures=5, last_market_data_at=FIXED_NOW),
            "api_failures",
        ),
        (
            HealthStatus(FIXED_NOW, last_market_data_at=FIXED_NOW - timedelta(hours=1)),
            "stale_market_data",
        ),
        (
            HealthStatus(FIXED_NOW, stablecoin_price=D("0.95"), last_market_data_at=FIXED_NOW),
            "stablecoin_depeg",
        ),
    ],
)
def test_blockers_suspend_new_offers_and_cancel_unfilled(health: HealthStatus, code: str) -> None:
    pf = _pf("5000", offers=[_offer("o1", "1000")], credits=[_credit("4000")])
    v = RiskManager().evaluate(Decision((_req("1000"),)), pf, state(FIXED_NOW), health)
    assert v.new_offers_blocked
    assert v.decision.place == ()
    assert v.decision.cancel == ("o1",)  # only unfilled offers; credits untouched
    assert code in {a.code for a in v.alerts}


def test_emergency_message_does_not_imply_credit_recall() -> None:
    health = HealthStatus(FIXED_NOW, emergency_stop=True, last_market_data_at=FIXED_NOW)
    v = RiskManager().evaluate(Decision(), _pf(), state(FIXED_NOW), health)
    assert CREDITS_NOT_RECALLABLE in v.alerts[0].message


def test_gate_for_backtests() -> None:
    gate = RiskManager(RiskLimits(max_single_offer=D(500))).as_gate()
    out = gate(Decision((_req("1000"),)), _pf(), state(FIXED_NOW))
    assert out.place[0].amount == D(500)


def test_invalid_limits() -> None:
    with pytest.raises(ValueError):
        RiskLimits(max_allocation_fraction=D("1.5"))
