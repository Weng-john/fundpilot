"""Exchange constraints applied identically to every strategy's decision.

Defaults reflect Bitfinex support documentation as seen through search summaries
(2-120 day period, minimum offer USD 150 equivalent, 15 % lender fee / 18 % hidden);
see docs/bitfinex-integration.md (A12-A14). For USDt the USD 150 minimum is treated
as 150 units, which assumes the peg holds (inference).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from fundpilot.core.money import quantize_down
from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.strategies.base import Decision, OfferRequest


@dataclass(frozen=True, slots=True)
class FundingConstraints:
    min_offer_amount: Decimal = Decimal(150)
    min_period_days: int = 2
    max_period_days: int = 120
    amount_places: int = 8
    lender_fee: Decimal = Decimal("0.15")
    max_offer_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Rejection:
    request: OfferRequest | None
    reason: str


def enforce(
    decision: Decision, portfolio: PortfolioState, constraints: FundingConstraints
) -> tuple[Decision, list[Rejection]]:
    """Return a decision that is feasible for ``portfolio`` plus the reasons for any drops.

    Cancels are applied first (unknown ids are dropped), so released capital is available to
    new offers in the same decision. Offer amounts are rounded *down*; requests are processed
    in order and capped by remaining capital.
    """
    rejections: list[Rejection] = []
    known = {o.offer_id: o for o in portfolio.offers}
    cancels = []
    for offer_id in dict.fromkeys(decision.cancel):
        if offer_id in known:
            cancels.append(offer_id)
        else:
            rejections.append(Rejection(None, f"cancel of unknown offer {offer_id}"))
    remaining = portfolio.available + sum((known[i].amount for i in cancels), Decimal(0))

    accepted: list[OfferRequest] = []
    for req in decision.place:
        reason = _invalid(req, constraints)
        if reason:
            rejections.append(Rejection(req, reason))
            continue
        amount = min(req.amount, remaining)
        if constraints.max_offer_amount is not None:
            amount = min(amount, constraints.max_offer_amount)
        amount = quantize_down(amount, constraints.amount_places)
        if amount < constraints.min_offer_amount:
            rejections.append(
                Rejection(req, f"amount {amount} below minimum {constraints.min_offer_amount}")
            )
            continue
        accepted.append(replace(req, amount=amount))
        remaining -= amount
    return Decision(tuple(accepted), tuple(cancels), decision.notes), rejections


def _invalid(req: OfferRequest, c: FundingConstraints) -> str | None:
    if not c.min_period_days <= req.period_days <= c.max_period_days:
        return f"period {req.period_days} outside [{c.min_period_days}, {c.max_period_days}]"
    if req.amount <= 0:
        return "non-positive amount"
    if req.rate_type is RateType.FIXED and (req.rate is None or req.rate <= 0):
        return "fixed-rate offer needs a positive rate"
    if req.rate_type is RateType.FRR and req.rate is not None:
        return "FRR offer must not specify a rate"
    return None


def split_amount(total: Decimal, chunk: Decimal | None, minimum: Decimal) -> list[Decimal]:
    """Split ``total`` into chunks of at most ``chunk``; a tail below ``minimum`` is merged."""
    if total <= 0:
        return []
    if chunk is None or total <= chunk:
        return [total]
    parts: list[Decimal] = []
    left = total
    while left > 0:
        part = min(chunk, left)
        parts.append(part)
        left -= part
    if len(parts) > 1 and parts[-1] < minimum:
        tail = parts.pop()
        parts[-1] += tail
    return parts
