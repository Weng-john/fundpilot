"""Backtest supply strategies over a series of Aave reserve snapshots. Results are SIMULATED.

Assumptions (explicit):

* Input snapshots describe the reserves *without* our capital. Our own deposits change the supply
  APR through the reserve's rate model (borrow demand held constant). Without model parameters
  the observed APR is used (no impact).
* Interest accrues linearly between steps at the step-start APR (Aave's linear supply accrual).
* A withdrawal can take at most the reserve's available liquidity at that step (by default the
  *observed* liquidity, conservatively ignoring the liquidity our own deposit added); the rest
  stays and the event is counted as "blocked".
* Move costs (gas, bridge) are paid out of the moved amount, in token units (~USD).
* No smart-contract, depeg, bridge or oracle failure is modelled; those risks are real and
  not captured by these numbers.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from fundpilot.core.models import DataOrigin
from fundpilot.defi.aave.math import SECONDS_PER_YEAR, RateModel, rates
from fundpilot.defi.aave.models import ReserveSnapshot
from fundpilot.defi.aave.strategy import AllocatorConfig, Plan, move_cost, plan

ZERO = Decimal(0)
Snapshots = Mapping[str, ReserveSnapshot]


class Policy(Protocol):
    @property
    def name(self) -> str: ...

    def target(
        self, snaps: Snapshots, positions: Mapping[str, Decimal], wallet: tuple[str, Decimal] | None
    ) -> dict[str, Decimal] | None:
        """Desired positions (None = keep)."""
        ...


@dataclass(slots=True)
class StaticPolicy:
    """Supply everything to one market and never move."""

    key: str

    @property
    def name(self) -> str:
        return f"static({self.key})"

    def target(
        self, snaps: Snapshots, positions: Mapping[str, Decimal], wallet: tuple[str, Decimal] | None
    ) -> dict[str, Decimal] | None:
        if wallet is None or wallet[1] <= 0:
            return None
        total = sum(positions.values(), ZERO) + wallet[1]
        return {self.key: total}


@dataclass(slots=True)
class ChaserPolicy:
    """Naive: move everything to the highest *observed* APR, ignoring impact and costs."""

    symbol: str

    @property
    def name(self) -> str:
        return "apy_chaser"

    def target(
        self, snaps: Snapshots, positions: Mapping[str, Decimal], wallet: tuple[str, Decimal] | None
    ) -> dict[str, Decimal] | None:
        cands = [s for s in snaps.values() if s.symbol == self.symbol and s.tradable]
        if not cands:
            return None
        best = max(cands, key=lambda s: s.supply_apr).key
        total = sum(positions.values(), ZERO) + (wallet[1] if wallet else ZERO)
        return {best: total}


@dataclass(slots=True)
class AllocatorPolicy:
    symbol: str
    config: AllocatorConfig = field(default_factory=AllocatorConfig)
    last_plan: Plan | None = None

    @property
    def name(self) -> str:
        return "impact_cost_allocator"

    def target(
        self, snaps: Snapshots, positions: Mapping[str, Decimal], wallet: tuple[str, Decimal] | None
    ) -> dict[str, Decimal] | None:
        self.last_plan = plan(self.symbol, snaps, positions, self.config, wallet=wallet)
        return self.last_plan.target if self.last_plan.adopted else None


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    symbol: str
    capital: Decimal
    start_chain: str  # where the capital sits initially (wallet)
    rebalance_every: int = 24  # steps
    costs: AllocatorConfig = field(default_factory=AllocatorConfig)
    # "observed" (conservative): a withdrawal can take at most the liquidity observed in the
    # historical snapshot - as if borrowers would also absorb our deposit. "with_us": our own
    # deposit is always withdrawable (borrow demand constant), so lock-ups never occur.
    withdrawal_liquidity: str = "observed"


@dataclass(slots=True)
class BacktestResult:
    policy: str
    symbol: str
    start: datetime
    end: datetime
    initial: Decimal
    final: Decimal
    interest: Decimal
    costs: Decimal
    moves: int
    blocked_withdrawals: int
    equity: list[tuple[datetime, Decimal]]
    allocation_history: list[tuple[datetime, dict[str, Decimal]]]
    kind: str = "simulated"

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86_400

    @property
    def net_apr(self) -> float:
        """Simple annualised net return (after costs)."""
        if self.days <= 0:
            return 0.0
        return float((self.final - self.initial) / self.initial) * 365 / self.days

    @property
    def net_apy(self) -> float:
        if self.days <= 0 or self.final <= 0:
            return 0.0
        return math.exp(math.log(float(self.final / self.initial)) * 365 / self.days) - 1


def _with_us(s: ReserveSnapshot, ours: Decimal) -> ReserveSnapshot:
    """The on-chain view including our position (what a live reader would observe)."""
    if ours <= 0:
        return s
    apr = s.supply_apr_with(ours)
    return s.model_copy(
        update={
            "available_liquidity": s.available_liquidity + ours,
            "total_supplied": s.total_supplied + ours,
            "supply_apr": apr,
        }
    )


def simulate(
    series: Sequence[tuple[datetime, Snapshots]], policy: Policy, cfg: BacktestConfig
) -> BacktestResult:
    if len(series) < 2:
        raise ValueError("need at least two snapshot times")
    positions: dict[str, Decimal] = {}
    wallet: tuple[str, Decimal] | None = (cfg.start_chain, cfg.capital)
    interest = costs = ZERO
    moves = blocked = 0
    equity: list[tuple[datetime, Decimal]] = []
    history: list[tuple[datetime, dict[str, Decimal]]] = []
    prev_t: datetime | None = None
    prev_snaps: Snapshots = {}
    for step, (t, snaps) in enumerate(series):
        if prev_t is not None:
            dt = Decimal((t - prev_t).total_seconds())
            for k, amt in list(positions.items()):
                s = prev_snaps.get(k)
                if s is None or amt <= 0:
                    continue
                gain = amt * s.supply_apr_with(amt) * dt / SECONDS_PER_YEAR
                positions[k] = amt + gain
                interest += gain
        if step % cfg.rebalance_every == 0:
            observed = {k: _with_us(s, positions.get(k, ZERO)) for k, s in snaps.items()}
            target = policy.target(observed, positions, wallet)
            if target is not None:
                spent, n, b, wallet = _execute(positions, target, wallet, snaps, cfg)
                costs += spent
                moves += n
                blocked += b
        total = sum(positions.values(), ZERO) + (wallet[1] if wallet else ZERO)
        equity.append((t, total))
        history.append((t, dict(positions)))
        prev_t, prev_snaps = t, snaps
    return BacktestResult(
        policy.name,
        cfg.symbol,
        series[0][0],
        series[-1][0],
        cfg.capital,
        equity[-1][1],
        interest,
        costs,
        moves,
        blocked,
        equity,
        history,
    )


def _execute(  # noqa: PLR0912 - explicit step-by-step settlement
    positions: dict[str, Decimal],
    target: Mapping[str, Decimal],
    wallet: tuple[str, Decimal] | None,
    snaps: Snapshots,
    cfg: BacktestConfig,
) -> tuple[Decimal, int, int, tuple[str, Decimal] | None]:
    spent = ZERO
    n = blocked = 0
    # 1) withdraw excess (bounded by liquidity incl. our own deposit)
    freed: list[tuple[str, Decimal]] = []
    for k, amt in list(positions.items()):
        excess = amt - target.get(k, ZERO)
        if excess <= 0:
            continue
        s = snaps.get(k)
        if s is None:
            liquidity = ZERO
        elif cfg.withdrawal_liquidity == "with_us":
            liquidity = s.available_liquidity + amt
        else:
            liquidity = s.available_liquidity
        take = min(excess, liquidity)
        if take < excess:
            blocked += 1
        if take > 0:
            positions[k] = amt - take
            freed.append((k, take))
    if wallet and wallet[1] > 0:
        freed.append((f"wallet:{wallet[0]}", wallet[1]))
        wallet = None
    # 2) deposit into markets below target
    for k, want in target.items():
        need = want - positions.get(k, ZERO)
        while need > 0 and freed:
            src, amount = freed[0]
            use = min(amount, need)
            cost = min(use, move_cost(src, k, cfg.costs))
            positions[k] = positions.get(k, ZERO) + use - cost
            spent += cost
            n += 1
            need -= use
            if use == amount:
                freed.pop(0)
            else:
                freed[0] = (src, amount - use)
    # anything left (e.g. target smaller than capital) returns to its source market/wallet
    for src, amount in freed:
        if src.startswith("wallet:"):
            wallet = (src.removeprefix("wallet:"), amount)
        else:
            positions[src] = positions.get(src, ZERO) + amount
    for k in [k for k, v in positions.items() if v <= 0]:
        del positions[k]
    return spent, n, blocked, wallet


# ---------------------------------------------------------------------------
# SYNTHETIC series for demos/tests (not market data)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SyntheticMarket:
    chain: str
    total_supplied: Decimal
    mean_usage: float
    model: RateModel
    reserve_factor: Decimal = Decimal("0.1")


def synthetic_series(
    markets: Sequence[SyntheticMarket],
    symbol: str,
    *,
    start: datetime,
    steps: int,
    step: timedelta = timedelta(hours=1),
    seed: int = 0,
    shock_probability: float = 0.01,
) -> list[tuple[datetime, dict[str, ReserveSnapshot]]]:
    """Mean-reverting utilisation with occasional demand shocks. Tagged origin=synthetic."""
    rng = random.Random(seed)  # noqa: S311 - synthetic data
    usage = {m.chain: m.mean_usage for m in markets}
    out = []
    for i in range(steps):
        t = start + step * i
        snaps = {}
        for m in markets:
            u = usage[m.chain]
            u += 0.05 * (m.mean_usage - u) + rng.gauss(0, 0.01)
            if rng.random() < shock_probability:
                u += 0.12
            u = min(0.995, max(0.05, u))
            usage[m.chain] = u
            debt = (m.total_supplied * Decimal(f"{u:.6f}")).quantize(Decimal(1))
            available = m.total_supplied - debt
            s_apr, b_apr = rates(m.model, debt, available, m.reserve_factor)
            snaps[f"{m.chain}:{symbol}"] = ReserveSnapshot(
                chain=m.chain,
                symbol=symbol,
                underlying="0x" + "0" * 40,
                observed_at=t,
                total_supplied=m.total_supplied,
                total_debt=debt,
                available_liquidity=available,
                supply_apr=s_apr,
                borrow_apr=b_apr,
                reserve_factor=m.reserve_factor,
                supply_cap=None,
                optimal_usage=m.model.optimal_usage,
                base_rate=m.model.base_rate,
                slope1=m.model.slope1,
                slope2=m.model.slope2,
                origin=DataOrigin.SYNTHETIC,
            )
        out.append((t, snaps))
    return out


DEMO_START = datetime(2026, 1, 1, tzinfo=UTC)
