from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from fundpilot.backtesting.metrics import compute_metrics, excess_return
from fundpilot.backtesting.scenarios import NEUTRAL
from fundpilot.backtesting.simulator import (
    FillEvent,
    SimulationConfig,
    SimulationResult,
    Snapshot,
)
from fundpilot.core.portfolio import RateType
from tests.conftest import FIXED_NOW

D = Decimal


def _snap(
    day: int, available: str, lent: str, gross: str = "0", fees: str = "0", pending: str = "0"
) -> Snapshot:
    return Snapshot(
        FIXED_NOW + timedelta(days=day),
        D(available),
        D(0),
        D(lent),
        D(pending),
        D(gross),
        D(fees),
        D(0),
        D(1) if D(lent) else D(0),
    )


def _result(snaps: list[Snapshot], cost: str = "0") -> SimulationResult:
    cfg = SimulationConfig(initial_capital=D(1000), operating_cost_per_day=D(cost))
    return SimulationResult("s", NEUTRAL, cfg, snapshots=snaps)


def test_utilisation_and_yield() -> None:
    # half the time fully lent, half idle; 365 days -> annualised == raw ratio
    snaps = [
        _snap(0, "0", "1000"),
        _snap(182, "1000", "0"),
        _snap(365, "1000", "0", gross="20", fees="3", pending="17"),
    ]
    snaps[2] = Snapshot(snaps[2].at, D(1000), D(0), D(0), D(17), D(20), D(3), D(0), D(0))
    m = compute_metrics(_result(snaps))
    assert m.days == D(365)
    q = D("1e-12")
    assert m.capital_utilization.quantize(q) == (D(182) / D(365)).quantize(q)
    assert m.idle_capital_ratio.quantize(q) == (D(183) / D(365)).quantize(q)
    assert m.net_interest == D(17)
    assert m.gross_annualized_yield.quantize(D("0.0001")) == D("0.0200")
    assert m.net_annualized_yield.quantize(D("0.0001")) == D("0.0170")
    assert m.net_apy is not None and m.net_apy.quantize(D("0.0001")) == D("0.0170")


def test_costs_and_excess() -> None:
    snaps = [_snap(0, "1000", "0"), _snap(365, "1000", "0", gross="10", fees="0", pending="10")]
    m = compute_metrics(_result(snaps, cost="0.01"))
    assert m.operating_costs == D("3.65")
    base = compute_metrics(_result(snaps))
    assert excess_return(m, base) < 0


def test_drawdown_and_fill_times() -> None:
    snaps = [_snap(0, "1000", "0"), _snap(1, "900", "0"), _snap(2, "1000", "0")]
    r = _result(snaps)
    r.offered_amount = D(400)
    r.fills = [
        FillEvent(FIXED_NOW, "o1", "c1", D(100), D("0.0002"), RateType.FIXED, 2, 60.0, "trade"),
        FillEvent(FIXED_NOW, "o1", "c2", D(100), D("0.0002"), RateType.FIXED, 2, 180.0, "trade"),
        FillEvent(FIXED_NOW, "o2", "c3", D(200), D("0.0002"), RateType.FIXED, 2, 600.0, "trade"),
    ]
    m = compute_metrics(r)
    assert m.max_drawdown == D("0.1")
    assert m.fill_ratio == D(1)
    assert m.median_time_to_first_fill_seconds == 330.0
    assert m.mean_time_to_fill_seconds == pytest.approx((60 * 100 + 180 * 100 + 600 * 200) / 400)


def test_requires_two_snapshots() -> None:
    with pytest.raises(ValueError):
        compute_metrics(_result([_snap(0, "1", "0")]))
