"""Aave V3 rate math, transcribed from aave-dao/aave-v3-origin (main, fetched 2026-11-04).

* Rates are annual (APR) values in ray (1e27). ``SECONDS_PER_YEAR = 365 days``.
* ``DefaultReserveInterestRateStrategyV2.calculateInterestRates``:
  ``U = debt / (available + debt)``;
  ``borrow = base + slope1 * U / Uopt`` if ``U <= Uopt`` else
  ``base + slope1 + slope2 * (U - Uopt) / (1 - Uopt)``;
  ``supply = borrow * U_supply * (1 - reserveFactor)`` (unbacked deprecated, so
  ``U_supply == U``).
* Supply balances grow linearly between index updates (``calculateLinearInterest``); as
  indexes update on every interaction, compounding per update is the practical effect.

Computation uses ``Decimal`` fractions instead of integer ray arithmetic, so results may differ
from on-chain values in the last digits (rounding), which is immaterial for strategy decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

RAY = Decimal(10) ** 27
BPS = Decimal(10_000)
SECONDS_PER_YEAR = Decimal(365 * 86_400)


def ray_to_fraction(value: int | Decimal) -> Decimal:
    return Decimal(value) / RAY


@dataclass(frozen=True, slots=True)
class RateModel:
    """``InterestRateData`` (stored in bps on-chain) as fractions."""

    optimal_usage: Decimal
    base_rate: Decimal
    slope1: Decimal
    slope2: Decimal

    @classmethod
    def from_bps(cls, optimal: int, base: int, slope1: int, slope2: int) -> RateModel:
        return cls(
            Decimal(optimal) / BPS,
            Decimal(base) / BPS,
            Decimal(slope1) / BPS,
            Decimal(slope2) / BPS,
        )

    def __post_init__(self) -> None:
        if not Decimal(0) < self.optimal_usage < Decimal(1):
            raise ValueError("optimal usage must be in (0, 1)")

    def borrow_rate(self, usage: Decimal) -> Decimal:
        if usage <= self.optimal_usage:
            return self.base_rate + self.slope1 * usage / self.optimal_usage
        excess = (usage - self.optimal_usage) / (1 - self.optimal_usage)
        return self.base_rate + self.slope1 + self.slope2 * excess


def usage_ratio(total_debt: Decimal, available_liquidity: Decimal) -> Decimal:
    if total_debt <= 0:
        return Decimal(0)
    return total_debt / (available_liquidity + total_debt)


def rates(
    model: RateModel, total_debt: Decimal, available_liquidity: Decimal, reserve_factor: Decimal
) -> tuple[Decimal, Decimal]:
    """(supply APR, variable borrow APR) as fractions, mirroring ``calculateInterestRates``."""
    with localcontext() as ctx:
        ctx.prec = 40
        if total_debt <= 0:
            return Decimal(0), model.base_rate
        u = usage_ratio(total_debt, available_liquidity)
        borrow = model.borrow_rate(u)
        return borrow * u * (1 - reserve_factor), borrow


def supply_apr_after(
    model: RateModel,
    total_debt: Decimal,
    available_liquidity: Decimal,
    reserve_factor: Decimal,
    delta: Decimal,
) -> Decimal:
    """Supply APR after we add (``delta > 0``) or withdraw (``delta < 0``) liquidity.

    Borrowers are held constant, so this is the immediate impact of our own flow.
    """
    available = available_liquidity + delta
    if available < 0:
        raise ValueError("cannot withdraw more than the available liquidity")
    return rates(model, total_debt, available, reserve_factor)[0]


def apr_to_apy(apr: Decimal, compounding_per_year: Decimal = SECONDS_PER_YEAR) -> Decimal:
    """APY from APR with the given compounding frequency (per second by default).

    Per-second compounding is a common presentation (inference); actual compounding follows
    the reserve's index updates.
    """
    with localcontext() as ctx:
        ctx.prec = 40
        n = compounding_per_year
        return (1 + apr / n) ** int(n) - 1


def accrue(principal: Decimal, apr: Decimal, seconds: Decimal) -> Decimal:
    """Linear supply interest over ``seconds`` (``calculateLinearInterest``)."""
    return principal * apr * seconds / SECONDS_PER_YEAR
