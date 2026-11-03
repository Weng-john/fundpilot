"""Simple baseline strategies. Any "smarter" strategy has to beat these net of costs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.strategies.base import Decision, MarketState, OfferRequest
from fundpilot.strategies.constraints import split_amount


def _offer_idle(
    portfolio: PortfolioState,
    *,
    rate_type: RateType,
    rate: Decimal | None,
    period_days: int,
    keep: Decimal,
    min_amount: Decimal,
    max_offer_size: Decimal | None,
    tag: str,
) -> Decision:
    idle = portfolio.available - keep
    if idle < min_amount:
        return Decision()
    return Decision(
        place=tuple(
            OfferRequest(rate_type, rate, period_days, part, tag)
            for part in split_amount(idle, max_offer_size, min_amount)
        )
    )


@dataclass(slots=True)
class FixedRateStrategy:
    """Offer all idle capital at one fixed daily rate and period; never reprices."""

    rate: Decimal
    period_days: int = 2
    min_amount: Decimal = Decimal(150)
    max_offer_size: Decimal | None = None

    @property
    def name(self) -> str:
        return f"fixed_rate({self.rate}/d,{self.period_days}d)"

    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision:
        return _offer_idle(
            portfolio,
            rate_type=RateType.FIXED,
            rate=self.rate,
            period_days=self.period_days,
            keep=Decimal(0),
            min_amount=self.min_amount,
            max_offer_size=self.max_offer_size,
            tag="fixed",
        )


@dataclass(slots=True)
class FRRStrategy:
    """Offer all idle capital as variable-rate FRR offers."""

    period_days: int = 2
    min_amount: Decimal = Decimal(150)
    max_offer_size: Decimal | None = None

    @property
    def name(self) -> str:
        return f"frr({self.period_days}d)"

    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision:
        return _offer_idle(
            portfolio,
            rate_type=RateType.FRR,
            rate=None,
            period_days=self.period_days,
            keep=Decimal(0),
            min_amount=self.min_amount,
            max_offer_size=self.max_offer_size,
            tag="frr",
        )


@dataclass(slots=True)
class AutoRenewStrategy:
    """Approximation of exchange auto-renew: re-offer idle capital above ``keep_amount``.

    ``rate=None`` renews at FRR (variable); otherwise at the fixed rate. Unlike the
    exchange feature, timing is limited to the strategy's decision interval.
    """

    period_days: int = 2
    rate: Decimal | None = None
    keep_amount: Decimal = Decimal(0)
    min_amount: Decimal = Decimal(150)

    @property
    def name(self) -> str:
        kind = "frr" if self.rate is None else f"{self.rate}/d"
        return f"auto_renew({kind},{self.period_days}d,keep={self.keep_amount})"

    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision:
        rate_type = RateType.FRR if self.rate is None else RateType.FIXED
        return _offer_idle(
            portfolio,
            rate_type=rate_type,
            rate=self.rate,
            period_days=self.period_days,
            keep=self.keep_amount,
            min_amount=self.min_amount,
            max_offer_size=None,
            tag="auto_renew",
        )
