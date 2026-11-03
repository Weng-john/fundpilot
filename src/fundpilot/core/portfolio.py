"""Read-only portfolio views shared by strategies, the backtester and paper trading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class RateType(StrEnum):
    """``FIXED``: offer at an explicit daily rate. ``FRR``: variable Flash Return Rate offer."""

    FIXED = "fixed"
    FRR = "frr"


@dataclass(frozen=True, slots=True)
class OfferView:
    """An unfilled (or partially filled) funding offer."""

    offer_id: str
    symbol: str
    rate_type: RateType
    rate: Decimal | None  # None for FRR offers
    period_days: int
    amount: Decimal  # remaining, unfilled
    original_amount: Decimal
    created_at: datetime
    tag: str = ""

    def age(self, as_of: datetime) -> timedelta:
        return as_of - self.created_at


@dataclass(frozen=True, slots=True)
class CreditView:
    """Capital lent out to a borrower. It cannot be recalled by the lender."""

    credit_id: str
    symbol: str
    rate_type: RateType
    rate: Decimal  # fixed rate, or FRR at the last accrual for FRR credits
    period_days: int
    amount: Decimal
    opened_at: datetime
    expires_at: datetime
    tag: str = ""

    def remaining(self, as_of: datetime) -> timedelta:
        return max(timedelta(0), self.expires_at - as_of)


@dataclass(frozen=True, slots=True)
class PortfolioState:
    as_of: datetime
    currency: str
    available: Decimal  # idle capital in the funding wallet
    offers: tuple[OfferView, ...] = ()
    credits: tuple[CreditView, ...] = ()
    pending_interest: Decimal = Decimal(0)  # accrued, net of fees, not yet paid out

    @property
    def offered(self) -> Decimal:
        return sum((o.amount for o in self.offers), Decimal(0))

    @property
    def lent(self) -> Decimal:
        return sum((c.amount for c in self.credits), Decimal(0))

    @property
    def total(self) -> Decimal:
        """Capital excluding unpaid interest."""
        return self.available + self.offered + self.lent

    def allocated_by_period(self, period_days: int) -> Decimal:
        offered = sum((o.amount for o in self.offers if o.period_days == period_days), Decimal(0))
        lent = sum((c.amount for c in self.credits if c.period_days == period_days), Decimal(0))
        return offered + lent
