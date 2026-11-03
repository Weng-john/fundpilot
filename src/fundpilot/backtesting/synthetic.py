"""SYNTHETIC market data generator for demos and pipeline smoke tests.

The generated process (mean-reverting log rate with random spikes, Poisson trades)
is arbitrary and NOT calibrated to any real market. Every record is tagged
``origin=synthetic``; the backtest feed excludes it unless explicitly allowed,
and reports carry a SYNTHETIC banner. Never use it as evidence about real markets.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fundpilot.core.models import (
    BookSide,
    DataOrigin,
    FundingBookLevel,
    FundingBookSnapshot,
    FundingTicker,
    FundingTrade,
)

_PERIODS = (2, 7, 30, 120)


@dataclass(frozen=True, slots=True)
class SyntheticConfig:
    symbol: str = "fUST"
    start: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    days: int = 7
    step: timedelta = timedelta(minutes=1)
    base_daily_rate: float = 0.0002
    mean_reversion: float = 0.01
    volatility: float = 0.02
    spike_probability: float = 0.0005
    trades_per_step: float = 2.0
    seed: int = 42


def _d(x: float, places: int = 10) -> Decimal:
    return Decimal(f"{x:.{places}f}")


def generate(
    cfg: SyntheticConfig,
) -> tuple[list[FundingTicker], list[FundingBookSnapshot], list[FundingTrade]]:
    rng = random.Random(cfg.seed)  # noqa: S311 - synthetic data, not security
    tickers: list[FundingTicker] = []
    books: list[FundingBookSnapshot] = []
    trades: list[FundingTrade] = []
    log_base = math.log(cfg.base_daily_rate)
    x = log_base
    trade_id = 0
    t = cfg.start
    end = cfg.start + timedelta(days=cfg.days)
    while t < end:
        x += cfg.mean_reversion * (log_base - x) + cfg.volatility * rng.gauss(0, 1)
        if rng.random() < cfg.spike_probability:
            x += math.log(3)
        rate = math.exp(x)
        levels = []
        for k in range(10):
            period = _PERIODS[k % len(_PERIODS)]
            levels.append(
                FundingBookLevel(
                    side=BookSide.ASK,
                    rate=_d(rate * (1 + 0.02 * k)),
                    period_days=period,
                    amount=_d(rng.uniform(5e4, 5e5), 2),
                    count=rng.randint(1, 20),
                )
            )
            levels.append(
                FundingBookLevel(
                    side=BookSide.BID,
                    rate=_d(rate * (0.98 - 0.02 * k)),
                    period_days=period,
                    amount=_d(rng.uniform(1e4, 2e5), 2),
                    count=rng.randint(1, 5),
                )
            )
        books.append(
            FundingBookSnapshot(
                exchange="bitfinex",
                symbol=cfg.symbol,
                precision="P0",
                captured_at=t,
                levels=tuple(levels),
                origin=DataOrigin.SYNTHETIC,
            )
        )
        tickers.append(
            FundingTicker(
                exchange="bitfinex",
                symbol=cfg.symbol,
                observed_at=t,
                frr=_d(rate),
                bid=_d(rate * 0.98),
                bid_period_days=2,
                bid_size=None,
                ask=_d(rate),
                ask_period_days=2,
                ask_size=None,
                daily_change=None,
                daily_change_relative=None,
                last_price=_d(rate),
                volume=None,
                high=None,
                low=None,
                frr_amount_available=None,
                origin=DataOrigin.SYNTHETIC,
            )
        )
        n = _poisson(rng, cfg.trades_per_step)
        for _ in range(n):
            trade_id += 1
            offset = timedelta(seconds=rng.uniform(0, cfg.step.total_seconds()))
            trades.append(
                FundingTrade(
                    exchange="bitfinex",
                    symbol=cfg.symbol,
                    trade_id=trade_id,
                    executed_at=t + offset,
                    amount=_d(rng.expovariate(1 / 20_000), 2),
                    rate=_d(rate * rng.uniform(0.97, 1.08)),
                    period_days=rng.choice(_PERIODS),
                    origin=DataOrigin.SYNTHETIC,
                )
            )
        t += cfg.step
    return tickers, books, trades


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
