from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from fundpilot.defi.aave.backtest import (
    DEMO_START,
    AllocatorPolicy,
    BacktestConfig,
    ChaserPolicy,
    StaticPolicy,
    SyntheticMarket,
    simulate,
    synthetic_series,
)
from fundpilot.defi.aave.math import RateModel
from fundpilot.defi.aave.strategy import AllocatorConfig

D = Decimal
MODEL = RateModel.from_bps(9000, 0, 550, 3500)
NO_COST = AllocatorConfig(tx_cost={}, bridge_cost=D(0), min_gain=D(0))


def _series(steps: int = 24 * 10, seed: int = 1, shock: float = 0.0) -> list:  # type: ignore[type-arg]
    mk = [
        SyntheticMarket("ethereum", D(1_000_000_000), 0.80, MODEL),
        SyntheticMarket("arbitrum", D(100_000_000), 0.86, MODEL),
    ]
    return synthetic_series(
        mk, "USDC", start=DEMO_START, steps=steps, seed=seed, shock_probability=shock
    )


def test_static_interest_matches_linear_accrual_without_impact() -> None:
    # constant usage, tiny capital -> interest ~ capital * apr * time
    series = _series(steps=25, shock=0.0)
    cfg = BacktestConfig("USDC", D(1000), "ethereum", rebalance_every=1000, costs=NO_COST)
    r = simulate(series, StaticPolicy("ethereum:USDC"), cfg)
    apr = series[0][1]["ethereum:USDC"].supply_apr
    expected_day = D(1000) * apr / 365
    assert float(r.interest) == pytest.approx(float(expected_day), rel=0.05)
    assert abs(r.final - (r.initial + r.interest - r.costs)) < D("1e-18")


def test_conservation_and_determinism() -> None:
    series = _series(seed=3, shock=0.05)
    cfg = BacktestConfig("USDC", D(1_000_000), "ethereum", rebalance_every=6)
    a = simulate(series, AllocatorPolicy("USDC"), cfg)
    b = simulate(series, AllocatorPolicy("USDC"), cfg)
    assert a.equity == b.equity
    assert abs(a.final - (a.initial + a.interest - a.costs)) < D("1e-12")
    for _, total in a.equity:
        assert total > 0


def test_own_capital_lowers_achieved_apr() -> None:
    series = _series(steps=24 * 5)
    small = simulate(
        series,
        StaticPolicy("arbitrum:USDC"),
        BacktestConfig("USDC", D(10_000), "arbitrum", costs=NO_COST),
    )
    huge = simulate(
        series,
        StaticPolicy("arbitrum:USDC"),
        BacktestConfig("USDC", D(50_000_000), "arbitrum", costs=NO_COST),
    )
    assert huge.net_apr < small.net_apr  # depositing 50 % of the pool compresses the rate


def test_costs_make_naive_chasing_expensive() -> None:
    series = _series(steps=24 * 30, seed=2, shock=0.05)
    cfg = BacktestConfig("USDC", D(10_000), "ethereum", rebalance_every=12)
    chaser = simulate(series, ChaserPolicy("USDC"), cfg)
    alloc = simulate(series, AllocatorPolicy("USDC"), cfg)
    assert chaser.costs > alloc.costs
    assert chaser.moves > alloc.moves


def test_withdrawals_are_limited_by_liquidity() -> None:
    series = _series(steps=48)
    # drain ethereum liquidity in the second half and force a move away from it
    for _, snaps in series[24:]:
        s = snaps["ethereum:USDC"]
        snaps["ethereum:USDC"] = s.model_copy(update={"available_liquidity": D(100)})
    cfg = BacktestConfig("USDC", D(10_000), "ethereum", rebalance_every=24, costs=NO_COST)

    class FlipPolicy:
        name = "flip"
        calls = 0

        def target(self, snaps, positions, wallet):  # type: ignore[no-untyped-def]
            self.calls += 1
            total = sum(positions.values(), D(0)) + (wallet[1] if wallet else D(0))
            return {"ethereum:USDC": total} if self.calls == 1 else {"arbitrum:USDC": total}

    r = simulate(series, FlipPolicy(), cfg)
    assert r.blocked_withdrawals == 1
    last_alloc = r.allocation_history[-1][1]
    assert last_alloc["ethereum:USDC"] > D(9000)  # most capital stuck
    assert float(last_alloc["arbitrum:USDC"]) == pytest.approx(100, rel=0.02)
    lenient = simulate(
        series,
        FlipPolicy(),
        BacktestConfig(
            "USDC",
            D(10_000),
            "ethereum",
            rebalance_every=24,
            costs=NO_COST,
            withdrawal_liquidity="with_us",
        ),
    )
    assert lenient.blocked_withdrawals == 0


def test_spread_half_life_reduces_expected_gain() -> None:
    persistent = AllocatorConfig(spread_half_life_days=None, horizon_days=D(30))
    decaying = AllocatorConfig(spread_half_life_days=D(3), horizon_days=D(30))
    assert decaying.effective_years() < persistent.effective_years()
    assert persistent.effective_years() == D(30) / D(365)


def test_synthetic_series_is_tagged() -> None:
    series = _series(steps=3)
    assert all(s.origin.value == "synthetic" for _, snaps in series for s in snaps.values())
    assert series[1][0] - series[0][0] == timedelta(hours=1)


def test_targeting_a_market_without_data_fails_loudly() -> None:
    series = _series(steps=5)
    with pytest.raises(ValueError, match="without data"):
        simulate(
            series,
            StaticPolicy("polygon:USDC"),
            BacktestConfig("USDC", D(1000), "polygon", costs=NO_COST),
        )


def test_same_chain_wallet_deposit_has_no_bridge_cost() -> None:
    from fundpilot.defi.aave.strategy import move_cost  # noqa: PLC0415

    cfg = AllocatorConfig()
    assert move_cost("wallet:polygon", "polygon:USDT", cfg) == cfg.tx_cost["polygon"]
    assert move_cost("wallet:polygon", "arbitrum:USDT", cfg) == (
        cfg.tx_cost["arbitrum"] + cfg.bridge_cost
    )
    assert move_cost("polygon:USDT", "arbitrum:USDT", cfg) == (
        cfg.tx_cost["polygon"] + cfg.tx_cost["arbitrum"] + cfg.bridge_cost
    )
