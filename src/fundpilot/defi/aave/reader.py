"""Read Aave V3 reserve state through ``AaveProtocolDataProvider`` (read-only ``eth_call``).

Function signatures and return layouts from aave-dao/aave-v3-origin
``src/contracts/helpers/AaveProtocolDataProvider.sol`` and
``interfaces/IDefaultInterestRateStrategyV2.sol`` (main, fetched 2026-11-04). Deployed
versions differ per chain; calls that do not exist on an older deployment are optional.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from fundpilot.core.time import utc_now
from fundpilot.defi.aave.markets import Reserve
from fundpilot.defi.aave.math import BPS, RAY
from fundpilot.defi.aave.models import ReserveSnapshot
from fundpilot.defi.abi import call_data, decode_words, word_to_address
from fundpilot.defi.rpc import JsonRpcClient, RpcError

logger = logging.getLogger(__name__)

GET_RESERVE_DATA = "getReserveData(address)"  # 12 words
GET_RESERVE_CONFIGURATION = "getReserveConfigurationData(address)"  # 10 words
GET_RESERVE_CAPS = "getReserveCaps(address)"  # (borrowCap, supplyCap) whole tokens
GET_PAUSED = "getPaused(address)"
GET_VIRTUAL_BALANCE = "getVirtualUnderlyingBalance(address)"
GET_IR_STRATEGY = "getInterestRateStrategyAddress(address)"
GET_IR_DATA_BPS = "getInterestRateDataBps(address)"  # (optimal, base, slope1, slope2) bps


def read_snapshot(
    rpc: JsonRpcClient, reserve: Reserve, now: datetime | None = None
) -> ReserveSnapshot:
    dp = reserve.market.data_provider
    asset = reserve.underlying
    scale = Decimal(10) ** reserve.decimals

    data = decode_words(rpc.eth_call(dp, call_data(GET_RESERVE_DATA, asset)), 12)
    total_a, total_debt = Decimal(data[2]) / scale, Decimal(data[4]) / scale
    conf = decode_words(rpc.eth_call(dp, call_data(GET_RESERVE_CONFIGURATION, asset)), 10)
    if conf[0] != reserve.decimals:
        raise RpcError(f"{reserve.key}: decimals {conf[0]} != configured {reserve.decimals}")
    caps = decode_words(rpc.eth_call(dp, call_data(GET_RESERVE_CAPS, asset)), 2)
    paused = bool(decode_words(rpc.eth_call(dp, call_data(GET_PAUSED, asset)), 1)[0])
    try:
        available = (
            Decimal(decode_words(rpc.eth_call(dp, call_data(GET_VIRTUAL_BALANCE, asset)), 1)[0])
            / scale
        )
    except RpcError:
        logger.warning("%s: no virtual balance call; using aTokens - debt", reserve.key)
        available = max(Decimal(0), total_a - total_debt)
    model: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
    try:
        strategy = word_to_address(
            decode_words(rpc.eth_call(dp, call_data(GET_IR_STRATEGY, asset)), 1)[0]
        )
        ir = decode_words(rpc.eth_call(strategy, call_data(GET_IR_DATA_BPS, asset)), 4)
        model = tuple(Decimal(v) / BPS for v in ir)  # type: ignore[assignment]
    except RpcError as exc:
        logger.warning("%s: interest-rate parameters unavailable: %s", reserve.key, exc)
    return ReserveSnapshot(
        chain=reserve.market.chain,
        symbol=reserve.symbol,
        underlying=asset,
        observed_at=now or utc_now(),
        total_supplied=total_a,
        total_debt=total_debt,
        available_liquidity=available,
        supply_apr=Decimal(data[5]) / RAY,
        borrow_apr=Decimal(data[6]) / RAY,
        reserve_factor=Decimal(conf[4]) / BPS,
        supply_cap=None if caps[1] == 0 else Decimal(caps[1]),
        optimal_usage=model[0] if model else None,
        base_rate=model[1] if model else None,
        slope1=model[2] if model else None,
        slope2=model[3] if model else None,
        is_active=bool(conf[8]),
        is_frozen=bool(conf[9]),
        is_paused=paused,
    )
