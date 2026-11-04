from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from fundpilot.defi.aave import markets
from fundpilot.defi.aave.math import RAY, RateModel, accrue, apr_to_apy, rates, supply_apr_after
from fundpilot.defi.aave.models import ReserveSnapshot
from fundpilot.defi.aave.reader import read_snapshot
from fundpilot.defi.aave.strategy import AllocatorConfig, plan
from fundpilot.defi.abi import call_data, decode_words, encode_words, keccak256, selector
from fundpilot.defi.rpc import JsonRpcClient
from tests.conftest import FIXED_NOW

D = Decimal
MODEL = RateModel.from_bps(9000, 0, 600, 6000)  # Uopt 90 %, base 0, slope1 6 %, slope2 60 %


def test_keccak_known_vectors() -> None:
    assert keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert selector("transfer(address,uint256)") == "0xa9059cbb"
    assert selector("balanceOf(address)") == "0x70a08231"
    data = call_data("balanceOf(address)", "0xdAC17F958D2ee523a2206206994597C13D831ec7")
    assert data.endswith("dac17f958d2ee523a2206206994597c13d831ec7") and len(data) == 2 + 8 + 64
    assert decode_words(encode_words([1, 2**200]), 2) == [1, 2**200]
    with pytest.raises(ValueError):
        call_data("x(address)", "0x1234")


def test_rate_model_matches_contract_formula() -> None:
    # below optimal: borrow = slope1 * U / Uopt ; supply = borrow * U * (1 - RF)
    supply, borrow = rates(MODEL, D(45), D(55), D("0.1"))  # U = 45 %
    assert borrow == D("0.06") * D("0.45") / D("0.9")
    assert supply == borrow * D("0.45") * D("0.9")
    # above optimal: borrow = slope1 + slope2 * (U - Uopt) / (1 - Uopt)
    _, borrow_hi = rates(MODEL, D(95), D(5), D("0.1"))
    assert borrow_hi == D("0.06") + D("0.6") * (D("0.95") - D("0.9")) / D("0.1")
    assert rates(MODEL, D(0), D(100), D("0.1")) == (D(0), D(0))


def test_own_deposit_lowers_supply_rate() -> None:
    before = supply_apr_after(MODEL, D(800), D(200), D("0.1"), D(0))
    after = supply_apr_after(MODEL, D(800), D(200), D("0.1"), D(500))
    assert after < before
    with pytest.raises(ValueError):
        supply_apr_after(MODEL, D(800), D(200), D("0.1"), D(-300))


def test_apy_and_accrual() -> None:
    apy = apr_to_apy(D("0.05"))
    assert D("0.05127") < apy < D("0.05128")  # ~ e^0.05 - 1
    assert accrue(D(1000), D("0.0365"), D(86_400)) == D("0.1")


def test_markets_registry() -> None:
    syms = {(r.market.chain, r.symbol) for r in markets.reserves()}
    assert ("ethereum", "USDT") in syms and ("ethereum", "USDC") in syms
    assert ("base", "USDT") not in syms  # Base has no USDT reserve in the address book
    assert markets.find("arbitrum", "USDC").book_symbol == "USDCn"
    assert all(r.decimals == 6 for r in markets.RESERVES)


def _rpc(handler_map: dict[str, list[int] | Exception]) -> JsonRpcClient:
    def handler(req: httpx.Request) -> httpx.Response:
        import json  # noqa: PLC0415

        body = json.loads(req.content)
        data = body["params"][0]["data"][:10]
        value = handler_map.get(data)
        if isinstance(value, Exception) or value is None:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {"code": -32000, "message": "revert"},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": encode_words(value)}
        )

    return JsonRpcClient(
        "https://rpc.invalid", transport=httpx.MockTransport(handler), sleep=lambda _: None
    )


def test_reader_decodes_data_provider() -> None:
    reserve = markets.find("ethereum", "USDT")
    strategy = int("1" * 40, 16)
    rpc = _rpc(
        {
            selector("getReserveData(address)"): [
                0,
                0,
                1_000_000 * 10**6,
                0,
                700_000 * 10**6,
                int(D("0.04") * RAY),
                int(D("0.065") * RAY),
                0,
                0,
                10**27,
                10**27,
                1_700_000_000,
            ],
            selector("getReserveConfigurationData(address)"): [6, 0, 0, 0, 1000, 0, 1, 0, 1, 0],
            selector("getReserveCaps(address)"): [0, 2_000_000],
            selector("getPaused(address)"): [0],
            selector("getVirtualUnderlyingBalance(address)"): [300_000 * 10**6],
            selector("getInterestRateStrategyAddress(address)"): [strategy],
            selector("getInterestRateDataBps(address)"): [9000, 0, 600, 6000],
        }
    )
    s = read_snapshot(rpc, reserve, FIXED_NOW)
    assert (s.total_supplied, s.total_debt, s.available_liquidity) == (
        D(1_000_000),
        D(700_000),
        D(300_000),
    )
    assert s.supply_apr == D("0.04") and s.borrow_apr == D("0.065")
    assert s.reserve_factor == D("0.1") and s.supply_cap == D(2_000_000)
    assert s.utilization == D("0.7") and s.model == MODEL and s.tradable


def test_reader_tolerates_missing_optional_calls() -> None:
    reserve = markets.find("ethereum", "USDC")
    rpc = _rpc(
        {
            selector("getReserveData(address)"): [0, 0, 10**12, 0, 5 * 10**11, 0, 0, 0, 0, 0, 0, 0],
            selector("getReserveConfigurationData(address)"): [6, 0, 0, 0, 1000, 0, 1, 0, 1, 1],
            selector("getReserveCaps(address)"): [0, 0],
            selector("getPaused(address)"): [0],
        }
    )
    s = read_snapshot(rpc, reserve, FIXED_NOW)
    assert s.available_liquidity == D(500_000) and s.model is None
    assert s.supply_cap is None and s.is_frozen and not s.tradable


def snap(
    chain: str,
    symbol: str = "USDT",
    *,
    supplied: str = "1000000",
    debt: str = "800000",
    rf: str = "0.1",
    cap: str | None = None,
    frozen: bool = False,
    model: RateModel = MODEL,
) -> ReserveSnapshot:
    sup, dbt = D(supplied), D(debt)
    s_apr, b_apr = rates(model, dbt, sup - dbt, D(rf))
    return ReserveSnapshot(
        chain=chain,
        symbol=symbol,
        underlying="0x" + "0" * 40,
        observed_at=FIXED_NOW,
        total_supplied=sup,
        total_debt=dbt,
        available_liquidity=sup - dbt,
        supply_apr=s_apr,
        borrow_apr=b_apr,
        reserve_factor=D(rf),
        supply_cap=None if cap is None else D(cap),
        optimal_usage=model.optimal_usage,
        base_rate=model.base_rate,
        slope1=model.slope1,
        slope2=model.slope2,
        is_frozen=frozen,
    )


def test_allocator_splits_by_marginal_income() -> None:
    # two identical markets: a big deposit should be split, not dumped into one
    snaps = {"ethereum:USDT": snap("ethereum"), "arbitrum:USDT": snap("arbitrum")}
    cfg = AllocatorConfig(
        chunk=D(10_000),
        tx_cost={"ethereum": D(0), "arbitrum": D(0)},
        bridge_cost=D(0),
        min_gain=D(0),
    )
    p = plan("USDT", snaps, {}, cfg, wallet=("ethereum", D(400_000)))
    assert p.adopted
    assert p.target["ethereum:USDT"] == p.target["arbitrum:USDT"] == D(200_000)


def test_allocator_respects_costs_and_hysteresis() -> None:
    better = snap("arbitrum", debt="870000")  # higher utilisation -> higher APR
    snaps = {"ethereum:USDT": snap("ethereum"), "arbitrum:USDT": better}
    small = plan(
        "USDT", snaps, {"ethereum:USDT": D(1000)}, AllocatorConfig(chunk=D(100), horizon_days=D(7))
    )
    assert not small.adopted and small.target == small.current  # gain < gas + bridge
    large = plan(
        "USDT",
        snaps,
        {"ethereum:USDT": D(100_000)},
        AllocatorConfig(chunk=D(5000), horizon_days=D(30)),
    )
    assert large.adopted and large.target["arbitrum:USDT"] > 0
    assert all(m.cost > 0 for m in large.moves)


def test_allocator_guards() -> None:
    snaps = {
        "ethereum:USDT": snap("ethereum"),
        "arbitrum:USDT": snap("arbitrum", debt="950000"),  # utilisation 95 % > 92 %
        "polygon:USDT": snap("polygon", frozen=True),
        "optimism:USDT": snap("optimism", cap="1005000"),  # 5000 headroom
    }
    cfg = AllocatorConfig(chunk=D(1000), tx_cost={}, bridge_cost=D(0), min_gain=D(0))
    p = plan("USDT", snaps, {}, cfg, wallet=("ethereum", D(50_000)))
    assert p.target["arbitrum:USDT"] == 0 and p.target["polygon:USDT"] == 0
    assert p.target["optimism:USDT"] <= D(5000)
    assert sum(p.target.values()) == D(50_000)
    assert any("utilisation" in n for n in p.notes) and any("frozen" in n for n in p.notes)


def test_allocator_only_moves_one_symbol() -> None:
    snaps = {"ethereum:USDT": snap("ethereum"), "ethereum:USDC": snap("ethereum", "USDC")}
    p = plan(
        "USDC",
        snaps,
        {"ethereum:USDT": D(5000)},
        wallet=("ethereum", D(1000)),
        cfg=AllocatorConfig(min_gain=D(0)),
    )
    assert set(p.target) == {"ethereum:USDC"}
