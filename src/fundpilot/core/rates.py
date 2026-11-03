"""Funding-rate representation and conversions.

Internal convention
-------------------
Every funding rate inside FundPilot is a **daily rate expressed as a decimal
fraction** (``Decimal("0.0002")`` means 0.02 % per day). Exchange adapters are
responsible for converting their native units into this convention.

Annualised figures are *derived* values:

* ``APR = daily * 365`` (simple, no compounding)
* ``APY = (1 + daily) ** 365 - 1`` (assumes daily compounding and continuous
  full utilisation, which real lending rarely achieves; treat it as an upper
  bound, never as realised performance).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

DAYS_PER_YEAR = Decimal(365)
SECONDS_PER_DAY = Decimal(86_400)
_HUNDRED = Decimal(100)
_CALC_PRECISION = 50


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Strict conversion to ``Decimal``. Floats are rejected to avoid binary rounding."""
    if isinstance(value, bool):
        raise TypeError("bool is not a valid numeric value")
    if isinstance(value, float):
        raise TypeError("float is not allowed for financial values; use Decimal or str")
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite():
        raise ValueError(f"non-finite decimal value: {value!r}")
    return result


@dataclass(frozen=True, slots=True)
class DailyRate:
    """A funding rate expressed as a decimal fraction per day."""

    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", to_decimal(self.value))

    # -- constructors -------------------------------------------------
    @classmethod
    def from_daily_fraction(cls, value: Decimal | int | str) -> DailyRate:
        return cls(to_decimal(value))

    @classmethod
    def from_daily_percent(cls, value: Decimal | int | str) -> DailyRate:
        return cls(to_decimal(value) / _HUNDRED)

    @classmethod
    def from_apr(cls, apr_fraction: Decimal | int | str) -> DailyRate:
        """Build from a simple annual rate expressed as a fraction (0.10 = 10 % APR)."""
        with localcontext() as ctx:
            ctx.prec = _CALC_PRECISION
            return cls(to_decimal(apr_fraction) / DAYS_PER_YEAR)

    # -- conversions --------------------------------------------------
    def daily_percent(self) -> Decimal:
        return self.value * _HUNDRED

    def apr(self) -> Decimal:
        """Simple annual rate as a fraction."""
        return self.value * DAYS_PER_YEAR

    def apy(self, compounding_periods: int = 365) -> Decimal:
        """Compounded annual yield as a fraction (hypothetical upper bound)."""
        if compounding_periods <= 0:
            raise ValueError("compounding_periods must be positive")
        with localcontext() as ctx:
            ctx.prec = _CALC_PRECISION
            periodic = self.apr() / Decimal(compounding_periods)
            return (Decimal(1) + periodic) ** compounding_periods - Decimal(1)

    def net_of_fee(self, fee_fraction: Decimal) -> DailyRate:
        """Rate after the platform takes ``fee_fraction`` of earned interest."""
        fee = to_decimal(fee_fraction)
        if not Decimal(0) <= fee <= Decimal(1):
            raise ValueError("fee_fraction must be within [0, 1]")
        return DailyRate(self.value * (Decimal(1) - fee))

    def __lt__(self, other: DailyRate) -> bool:
        return self.value < other.value

    def __le__(self, other: DailyRate) -> bool:
        return self.value <= other.value


def accrued_interest(
    principal: Decimal, rate: DailyRate, elapsed_seconds: Decimal | int
) -> Decimal:
    """Gross simple interest accrued over ``elapsed_seconds``.

    Uses per-second pro-rating of the daily rate (``principal * rate * s / 86400``).
    No rounding is applied; quantise only at presentation or settlement boundaries.
    """
    principal = to_decimal(principal)
    seconds = to_decimal(elapsed_seconds)
    if principal < 0:
        raise ValueError("principal must be non-negative")
    if seconds < 0:
        raise ValueError("elapsed_seconds must be non-negative")
    with localcontext() as ctx:
        ctx.prec = _CALC_PRECISION
        return principal * rate.value * seconds / SECONDS_PER_DAY


def platform_fee(gross_interest: Decimal, fee_fraction: Decimal) -> Decimal:
    """Fee charged by the platform on earned interest."""
    gross = to_decimal(gross_interest)
    fee = to_decimal(fee_fraction)
    if not Decimal(0) <= fee <= Decimal(1):
        raise ValueError("fee_fraction must be within [0, 1]")
    return gross * fee


def net_interest(gross_interest: Decimal, fee_fraction: Decimal) -> Decimal:
    """Interest left to the lender after the platform fee."""
    gross = to_decimal(gross_interest)
    return gross - platform_fee(gross, fee_fraction)
