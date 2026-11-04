"""Cost- and impact-aware allocation of one stablecoin (USDT or USDC) across Aave V3 markets.

* **Own impact.** Supplying lowers a reserve's utilisation and therefore its supply APR. Using the
  reserve's interest-rate model (verified from the contracts), capital is allocated in chunks to
  the market with the highest *marginal* income ``(x+c)*apr(x+c) - x*apr(x)`` (water-filling).
* **Costs.** Moving between markets costs gas on both chains and, across chains, a bridge fee.
  A plan is adopted only if the expected income gain exceeds its cost plus ``min_gain``
  (hysteresis), otherwise current positions are kept. Because spreads mean-revert, the gain is
  the APR difference integrated over ``horizon_days`` with ``spread_half_life_days`` decay.
  Idle wallet capital earns nothing, so deploying it counts over the full horizon.
* **Guards.** No new capital into reserves that are frozen/paused/inactive, above
  ``max_utilization`` (withdrawals may be blocked when liquidity is exhausted), or without
  supply-cap headroom. Withdrawals are limited by the reserve's available liquidity.

Snapshots are assumed to *include* our current positions (they are part of on-chain liquidity);
the allocator removes them before evaluating alternatives.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from fundpilot.defi.aave.models import ReserveSnapshot

ZERO = Decimal(0)
# Chains with low transaction costs (user preference 2026-11-04: Ethereum mainnet excluded).
LOW_GAS_CHAINS: tuple[str, ...] = ("polygon", "arbitrum", "base", "optimism", "avalanche")


@dataclass(frozen=True, slots=True)
class AllocatorConfig:
    horizon_days: Decimal = Decimal(30)
    chunk: Decimal = Decimal(1000)
    max_utilization: Decimal = Decimal("0.92")
    cap_buffer: Decimal = Decimal("0.98")  # use at most 98 % of remaining cap headroom
    # per-chain cost (in token units, ~USD) of one supply or withdraw transaction
    tx_cost: Mapping[str, Decimal] = field(
        default_factory=lambda: {
            "ethereum": Decimal(8),
            "arbitrum": Decimal("0.3"),
            "base": Decimal("0.2"),
            "optimism": Decimal("0.2"),
            "polygon": Decimal("0.1"),
            "avalanche": Decimal("0.3"),
        }
    )
    bridge_cost: Decimal = Decimal(5)  # fixed cost per cross-chain transfer
    min_gain: Decimal = Decimal(1)  # extra expected gain required to act
    max_share: Decimal = Decimal(1)  # max share of capital in one market
    # chains the allocator may use (None = all). Low-gas default: Ethereum mainnet excluded.
    allowed_chains: tuple[str, ...] | None = LOW_GAS_CHAINS
    # Rate spreads between markets mean-revert; the expected benefit of a move is the spread
    # integrated with this half-life over the horizon (None = assume the spread persists).
    spread_half_life_days: Decimal | None = Decimal(3)

    def effective_years(self) -> Decimal:
        horizon = self.horizon_days
        hl = self.spread_half_life_days
        if hl is None or hl <= 0:
            return horizon / Decimal(365)
        ln2 = Decimal(2).ln()
        decay = Decimal(1) - (-(ln2 * horizon / hl)).exp()
        return hl / ln2 * decay / Decimal(365)


@dataclass(frozen=True, slots=True)
class Move:
    source: str  # market key "chain:SYMBOL" or "wallet:<chain>"
    target: str
    amount: Decimal
    cost: Decimal


@dataclass(frozen=True, slots=True)
class Plan:
    symbol: str
    current: dict[str, Decimal]
    target: dict[str, Decimal]
    moves: tuple[Move, ...]
    current_apr: Decimal  # capital-weighted, after own impact
    target_apr: Decimal
    expected_gain: Decimal  # over the horizon, before costs
    cost: Decimal
    adopted: bool
    notes: tuple[str, ...] = ()


def _excluding_us(s: ReserveSnapshot, ours: Decimal) -> ReserveSnapshot:
    return s.model_copy(
        update={
            "available_liquidity": max(ZERO, s.available_liquidity - ours),
            "total_supplied": max(ZERO, s.total_supplied - ours),
        }
    )


def _apr(s: ReserveSnapshot, amount: Decimal) -> Decimal:
    """Supply APR of a reserve (evaluated without us) if we hold ``amount`` in it."""
    return s.supply_apr_with(amount) if amount > 0 else s.supply_apr_with(ZERO)


def income(base: Mapping[str, ReserveSnapshot], alloc: Mapping[str, Decimal]) -> Decimal:
    """Annual income of an allocation, given reserves evaluated without our capital."""
    return sum((amt * _apr(base[k], amt) for k, amt in alloc.items() if amt > 0), ZERO)


def _chain(key: str) -> str:
    """Chain of a market key ``chain:SYMBOL`` or a wallet key ``wallet:chain``."""
    if key.startswith("wallet:"):
        return key.removeprefix("wallet:")
    return key.split(":", 1)[0]


def move_cost(source: str, target: str, cfg: AllocatorConfig) -> Decimal:
    src_chain, dst_chain = _chain(source), _chain(target)
    cost = cfg.tx_cost.get(dst_chain, Decimal(1))
    if not source.startswith("wallet:"):
        cost += cfg.tx_cost.get(src_chain, Decimal(1))
    if src_chain != dst_chain:
        cost += cfg.bridge_cost
    return cost


def plan(
    symbol: str,
    snapshots: Mapping[str, ReserveSnapshot],
    positions: Mapping[str, Decimal],
    cfg: AllocatorConfig | None = None,
    *,
    wallet: tuple[str, Decimal] | None = None,  # (chain, idle amount) not yet supplied
    snapshots_include_positions: bool = True,
) -> Plan:
    cfg = cfg or AllocatorConfig()
    snaps = {
        k: s
        for k, s in snapshots.items()
        if s.symbol == symbol and (cfg.allowed_chains is None or s.chain in cfg.allowed_chains)
    }
    current = {k: positions.get(k, ZERO) for k in snaps}
    base = (
        {k: _excluding_us(s, current[k]) for k, s in snaps.items()}
        if snapshots_include_positions
        else dict(snaps)
    )
    idle = wallet[1] if wallet else ZERO
    total = sum(current.values(), ZERO) + idle
    notes: list[str] = []

    def limit(k: str) -> Decimal:
        s = snaps[k]
        if not s.tradable or s.utilization > cfg.max_utilization:
            return current[k]  # may keep, may not add
        headroom = base[k].cap_headroom()
        cap = total * cfg.max_share
        if headroom is not None:
            cap = min(cap, current[k] + max(ZERO, (headroom - current[k]) * cfg.cap_buffer))
        return max(cap, ZERO)

    for k, s in snaps.items():
        if not s.tradable:
            notes.append(f"{k}: not open for new supply (active/frozen/paused)")
        elif s.utilization > cfg.max_utilization:
            notes.append(f"{k}: utilisation {s.utilization:.3f} above {cfg.max_utilization}")

    # water-filling by marginal income
    target = dict.fromkeys(snaps, ZERO)
    remaining = total
    limits = {k: limit(k) for k in snaps}
    while remaining > 0:
        step = min(cfg.chunk, remaining)
        best, best_gain = None, None
        for k in snaps:
            if target[k] + step > limits[k]:
                continue
            x = target[k]
            gain = (x + step) * _apr(base[k], x + step) - x * _apr(base[k], x)
            if best_gain is None or gain > best_gain:
                best, best_gain = k, gain
        if best is None:  # every market is at its limit: keep the rest where it is
            notes.append(f"{remaining} could not be placed within limits")
            for k in snaps:
                keep = min(current[k], remaining)
                target[k] += keep
                remaining -= keep
            break
        target[best] += step
        remaining -= step

    moves = _moves(current, target, wallet, base, cfg)
    cost = sum((m.cost for m in moves), ZERO)
    years = cfg.effective_years()
    cur_income = income(base, current)
    tgt_income = income(base, target)
    # Idle capital earns 0 % today; putting it to work is a persistent gain (full horizon).
    # Only the re-allocation spread on already-invested capital is assumed to mean-revert.
    avg_target_apr = tgt_income / total if total else ZERO
    idle_part = idle * avg_target_apr
    gain = (
        idle_part * cfg.horizon_days / Decimal(365) + (tgt_income - cur_income - idle_part) * years
    )
    adopted = bool(moves) and (gain - cost) > cfg.min_gain
    invested = sum(current.values(), ZERO)
    return Plan(
        symbol=symbol,
        current=current,
        target=target if adopted else dict(current),
        moves=moves if adopted else (),
        current_apr=cur_income / invested if invested else ZERO,
        target_apr=tgt_income / total if total else ZERO,
        expected_gain=gain,
        cost=cost,
        adopted=adopted,
        notes=tuple(notes),
    )


def _moves(
    current: Mapping[str, Decimal],
    target: Mapping[str, Decimal],
    wallet: tuple[str, Decimal] | None,
    base: Mapping[str, ReserveSnapshot],
    cfg: AllocatorConfig,
) -> tuple[Move, ...]:
    sources: dict[str, Decimal] = {}
    if wallet and wallet[1] > 0:
        sources[f"wallet:{wallet[0]}"] = wallet[1]
    for k, cur in current.items():
        excess = cur - target[k]
        if excess > 0:
            # withdrawals cannot exceed what the reserve can pay out right now
            sources[k] = min(excess, base[k].available_liquidity + cur)
    moves = []
    for sink in [k for k in target if target[k] > current[k]]:
        need = target[sink] - current[sink]
        for src, available in list(sources.items()):
            if need <= 0:
                break
            amount = min(available, need)
            if amount <= 0:
                continue
            moves.append(Move(src, sink, amount, move_cost(src, sink, cfg)))
            sources[src] = available - amount
            need -= amount
    return tuple(moves)
