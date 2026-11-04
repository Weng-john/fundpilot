"""Order-book-aware rate selection.

Chooses an offer rate from the current ask side: walk asks from the lowest rate up
until ``depth_ahead`` of competing supply would sit ahead of us, then undercut that
level slightly. If resting borrower bids already pay at least that rate, offer at the
best bid instead (expected to match immediately). Unfilled offers are repriced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from fundpilot.core.models import BookSide
from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.strategies.base import Decision, MarketState, OfferRequest


@dataclass(slots=True)
class OrderBookAwareStrategy:
    period_days: int = 2
    depth_ahead: Decimal = Decimal(500_000)
    undercut: Decimal = Decimal("0.0000001")
    min_rate: Decimal = Decimal("0.00001")
    reprice_after: timedelta = timedelta(minutes=30)
    reprice_tolerance: Decimal = Decimal("0.02")
    min_amount: Decimal = Decimal(150)

    @property
    def name(self) -> str:
        return f"orderbook_aware({self.period_days}d,depth={self.depth_ahead})"

    def target_rate(self, market: MarketState) -> Decimal | None:
        # competing supply: offers we would actually queue behind (same or shorter period)
        asks = sorted(
            (lv for lv in market.levels(BookSide.ASK) if lv.period_days <= self.period_days),
            key=lambda lv: lv.rate,
        )
        if not asks:
            return None
        cumulative = Decimal(0)
        chosen = asks[-1].rate
        for level in asks:
            cumulative += level.amount
            if cumulative >= self.depth_ahead:
                chosen = level.rate
                break
        rate = chosen - self.undercut
        # Only borrower bids whose requested period fits our offer can match it. Real books
        # often show long-period bids above short-period asks (observed 2026-11-03 on fUST).
        bids = [lv for lv in market.levels(BookSide.BID) if lv.period_days <= self.period_days]
        best_bid = max(bids, key=lambda lv: lv.rate) if bids else None
        if best_bid is not None and best_bid.rate >= rate:
            rate = best_bid.rate
        return max(rate, self.min_rate)

    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision:
        target = self.target_rate(market)
        if target is None:
            return Decision(notes=("no ask side in book",))
        cancels = tuple(
            o.offer_id
            for o in portfolio.offers
            if o.rate is not None
            and o.age(market.as_of) >= self.reprice_after
            and abs(o.rate - target) > target * self.reprice_tolerance
        )
        released = sum((o.amount for o in portfolio.offers if o.offer_id in cancels), Decimal(0))
        idle = portfolio.available + released
        places: tuple[OfferRequest, ...] = ()
        if idle >= self.min_amount:
            places = (OfferRequest(RateType.FIXED, target, self.period_days, idle, "book"),)
        return Decision(places, cancels, (f"target_rate={target}",))
