"""Event-driven funding simulator.

Per market step ``t`` (strictly increasing), in this order:

1. accrue interest on active credits over ``(prev, t]`` (FRR credits use the FRR
   observed at ``prev``), net of the platform fee;
2. early repayments (random, seeded) with a minimum-interest top-up, then maturities;
3. interest payout to available capital every ``payout_interval`` (reinvestment);
4. fills of live offers against public trades in ``(prev, t]`` after the queue ahead;
5. strategy decision (every ``decision_interval``), constraint enforcement, cancels and
   new offers (live after ``latency_steps``; immediate bid-crossing at activation);
6. snapshot.

Stale market steps (``market.stale``) are treated as a disruption: no fills and no
decisions; accrual and maturities continue.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from fundpilot.backtesting.scenarios import ExecutionScenario, PeriodMatch
from fundpilot.core.models import BookSide
from fundpilot.core.portfolio import CreditView, OfferView, PortfolioState, RateType
from fundpilot.core.rates import SECONDS_PER_DAY
from fundpilot.strategies.base import Decision, MarketState, OfferRequest, Strategy
from fundpilot.strategies.constraints import FundingConstraints, enforce


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    initial_capital: Decimal
    currency: str = "UST"
    decision_interval: timedelta = timedelta(minutes=5)
    # Payout frequency is an assumption (believed daily on Bitfinex; unverified).
    payout_interval: timedelta = timedelta(days=1)
    # Minimum interest charged on early repayment (per support docs via search: 1 hour).
    min_interest: timedelta = timedelta(hours=1)
    seed: int = 0
    operating_cost_per_day: Decimal = Decimal(0)


@dataclass(slots=True)
class _Offer:
    offer_id: str
    request: OfferRequest
    remaining: Decimal
    created_at: datetime
    active_from_step: int
    queue_ahead: Decimal = Decimal(0)
    activated: bool = False
    activated_at: datetime | None = None
    first_fill_at: datetime | None = None

    def view(self, symbol: str) -> OfferView:
        r = self.request
        return OfferView(
            self.offer_id,
            symbol,
            r.rate_type,
            r.rate,
            r.period_days,
            self.remaining,
            r.amount,
            self.created_at,
            r.tag,
        )


@dataclass(slots=True)
class _Credit:
    credit_id: str
    offer_id: str
    rate_type: RateType
    rate: Decimal
    period_days: int
    amount: Decimal
    opened_at: datetime
    expires_at: datetime
    tag: str
    gross_interest: Decimal = Decimal(0)

    def view(self, symbol: str) -> CreditView:
        return CreditView(
            self.credit_id,
            symbol,
            self.rate_type,
            self.rate,
            self.period_days,
            self.amount,
            self.opened_at,
            self.expires_at,
            self.tag,
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    at: datetime
    available: Decimal
    offered: Decimal
    lent: Decimal
    pending_interest: Decimal
    gross_interest: Decimal
    fees: Decimal
    paid_interest: Decimal
    weighted_remaining_lock_days: Decimal

    @property
    def equity(self) -> Decimal:
        return self.available + self.offered + self.lent + self.pending_interest


@dataclass(frozen=True, slots=True)
class FillEvent:
    at: datetime
    offer_id: str
    credit_id: str
    amount: Decimal
    rate: Decimal
    rate_type: RateType
    period_days: int
    seconds_since_offer: float
    source: str  # "trade" | "bid_cross"


@dataclass(frozen=True, slots=True)
class CreditClose:
    at: datetime
    credit_id: str
    amount: Decimal
    reason: str  # "matured" | "early_repayment"
    gross_interest: Decimal


@dataclass(slots=True)
class SimulationResult:
    strategy: str
    scenario: ExecutionScenario
    config: SimulationConfig
    snapshots: list[Snapshot] = field(default_factory=list)
    fills: list[FillEvent] = field(default_factory=list)
    closes: list[CreditClose] = field(default_factory=list)
    offers_placed: int = 0
    offers_cancelled: int = 0
    offered_amount: Decimal = Decimal(0)
    rejections: list[str] = field(default_factory=list)
    stale_steps: int = 0
    steps: int = 0
    kind: str = "simulated"


class FundingSimulator:
    def __init__(
        self,
        strategy: Strategy,
        scenario: ExecutionScenario,
        config: SimulationConfig,
        constraints: FundingConstraints | None = None,
    ) -> None:
        self.strategy = strategy
        self.scenario = scenario
        self.config = config
        self.constraints = constraints or FundingConstraints()
        self._rng = random.Random(config.seed)  # noqa: S311 - simulation, not security
        self._ids = 0
        self.available = config.initial_capital
        self.offers: dict[str, _Offer] = {}
        self.credits: dict[str, _Credit] = {}
        self.pending = Decimal(0)
        self.gross = Decimal(0)
        self.fees = Decimal(0)
        self.paid = Decimal(0)

    def _next_id(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}{self._ids}"

    # ------------------------------------------------------------------ run
    def run(self, feed: Iterable[MarketState]) -> SimulationResult:
        with localcontext() as ctx:
            ctx.prec = 40
            return self._run(feed)

    def _run(self, feed: Iterable[MarketState]) -> SimulationResult:
        result = SimulationResult(self.strategy.name, self.scenario, self.config)
        prev: MarketState | None = None
        last_decision: datetime | None = None
        last_payout = datetime.min.replace(tzinfo=UTC)
        for step, market in enumerate(feed):
            if prev is not None and market.as_of <= prev.as_of:
                raise ValueError("market states must be strictly increasing in time")
            if prev is None:
                last_payout = market.as_of
            else:
                self._accrue(prev, market.as_of)
                self._repayments(prev.as_of, market.as_of, result)
            if market.as_of - last_payout >= self.config.payout_interval:
                self.available += self.pending
                self.paid += self.pending
                self.pending = Decimal(0)
                last_payout = market.as_of

            if market.stale:
                result.stale_steps += 1
            else:
                self._activate(step, market, result)
                if prev is not None:
                    self._fill_from_trades(step, prev.as_of, market, result)
                if last_decision is None or (
                    market.as_of - last_decision >= self.config.decision_interval
                ):
                    self._decide(step, market, result)
                    last_decision = market.as_of
                    self._activate(step, market, result)

            result.snapshots.append(self._snapshot(market.as_of))
            result.steps += 1
            prev = market
        return result

    # ------------------------------------------------------------- accounting
    def _accrue(self, prev: MarketState, now: datetime) -> None:
        fee_rate = self.constraints.lender_fee
        for credit in self.credits.values():
            start = max(prev.as_of, credit.opened_at)
            end = min(now, credit.expires_at)
            if end <= start:
                continue
            if credit.rate_type is RateType.FRR and prev.frr is not None and prev.frr > 0:
                credit.rate = prev.frr
            self._book_interest(credit, Decimal((end - start).total_seconds()), fee_rate)

    def _book_interest(self, credit: _Credit, seconds: Decimal, fee_rate: Decimal) -> None:
        gross = credit.amount * credit.rate * seconds / SECONDS_PER_DAY
        fee = gross * fee_rate
        credit.gross_interest += gross
        self.gross += gross
        self.fees += fee
        self.pending += gross - fee

    def _repayments(self, prev: datetime, now: datetime, result: SimulationResult) -> None:
        hazard = self.scenario.early_repay_hazard_per_day
        days = (now - prev).total_seconds() / 86_400
        p_repay = 1 - math.exp(-float(hazard) * days) if hazard > 0 else 0.0
        for credit_id in list(self.credits):
            credit = self.credits[credit_id]
            if credit.expires_at <= now:
                self._close(credit, credit.expires_at, "matured", result)
                continue
            if p_repay and credit.opened_at < now and self._rng.random() < p_repay:
                elapsed = now - credit.opened_at
                if elapsed < self.config.min_interest:
                    topup = Decimal((self.config.min_interest - elapsed).total_seconds())
                    self._book_interest(credit, topup, self.constraints.lender_fee)
                self._close(credit, now, "early_repayment", result)

    def _close(self, credit: _Credit, at: datetime, reason: str, result: SimulationResult) -> None:
        del self.credits[credit.credit_id]
        self.available += credit.amount
        result.closes.append(
            CreditClose(at, credit.credit_id, credit.amount, reason, credit.gross_interest)
        )

    # ------------------------------------------------------------------ fills
    def _matching_rate(self, offer: _Offer, market: MarketState) -> Decimal | None:
        if offer.request.rate_type is RateType.FRR:
            return market.frr if market.frr is not None and market.frr > 0 else None
        return offer.request.rate

    def _period_ok(self, offer_period: int, other_period: int) -> bool:
        if self.scenario.period_match is PeriodMatch.EXACT:
            return other_period == offer_period
        return other_period <= offer_period

    def _rate_ok(self, other: Decimal, ours: Decimal) -> bool:
        return other > ours if self.scenario.strict_rate else other >= ours

    def _queue_ahead(self, rate: Decimal, period: int, market: MarketState) -> Decimal:
        ahead = Decimal(0)
        for level in market.levels(BookSide.ASK):
            if not self._period_ok(period, level.period_days):
                continue
            if level.rate < rate:
                ahead += level.amount
            elif level.rate == rate:
                ahead += level.amount * self.scenario.same_rate_queue_fraction
        return ahead

    def _activate(self, step: int, market: MarketState, result: SimulationResult) -> None:
        for offer in sorted(self.offers.values(), key=lambda o: o.created_at):
            if offer.activated or step < offer.active_from_step:
                continue
            offer.activated = True
            offer.activated_at = market.as_of
            rate = self._matching_rate(offer, market)
            if rate is None:
                continue
            offer.queue_ahead = self._queue_ahead(rate, offer.request.period_days, market)
            bids = [
                lv
                for lv in market.levels(BookSide.BID)
                if self._rate_ok(lv.rate, rate)
                and self._period_ok(offer.request.period_days, lv.period_days)
            ]
            size = sum((lv.amount for lv in bids), Decimal(0)) * self.scenario.bid_cross_fraction
            if size > 0:
                self._fill(
                    offer, min(size, offer.remaining), rate, market.as_of, "bid_cross", result
                )

    def _fill_from_trades(
        self, step: int, prev: datetime, market: MarketState, result: SimulationResult
    ) -> None:
        trades = [t for t in market.trades if prev < t.executed_at <= market.as_of]
        if not trades:
            return
        used: dict[int, Decimal] = {}
        live = [o for o in self.offers.values() if o.activated and o.remaining > 0]

        def sort_key(o: _Offer) -> tuple[Decimal, datetime]:
            r = self._matching_rate(o, market)
            return (r if r is not None else Decimal("Infinity"), o.created_at)

        for offer in sorted(live, key=sort_key):
            rate = self._matching_rate(offer, market)
            if rate is None:
                continue
            volume = Decimal(0)
            eligible = []
            for trade in trades:
                if offer.activated_at is not None and trade.executed_at <= offer.activated_at:
                    continue  # trade happened before the offer was live
                if self._rate_ok(trade.rate, rate) and self._period_ok(
                    offer.request.period_days, trade.period_days
                ):
                    left = abs(trade.amount) - used.get(trade.trade_id, Decimal(0))
                    if left > 0:
                        eligible.append((trade, left))
                        volume += left
            if volume == 0:
                continue
            through_queue = min(volume, offer.queue_ahead)
            offer.queue_ahead -= through_queue
            capturable = (volume - through_queue) * self.scenario.participation
            amount = min(offer.remaining, capturable)
            if amount <= 0:
                continue
            # attribute consumed volume to trades in order (queue + our fill)
            to_consume = through_queue + amount
            for trade, left in eligible:
                take = min(left, to_consume)
                used[trade.trade_id] = used.get(trade.trade_id, Decimal(0)) + take
                to_consume -= take
                if to_consume <= 0:
                    break
            self._fill(offer, amount, rate, market.as_of, "trade", result)

    def _fill(
        self,
        offer: _Offer,
        amount: Decimal,
        rate: Decimal,
        at: datetime,
        source: str,
        result: SimulationResult,
    ) -> None:
        offer.remaining -= amount
        if offer.first_fill_at is None:
            offer.first_fill_at = at
        credit = _Credit(
            credit_id=self._next_id("c"),
            offer_id=offer.offer_id,
            rate_type=offer.request.rate_type,
            rate=rate,
            period_days=offer.request.period_days,
            amount=amount,
            opened_at=at,
            expires_at=at + timedelta(days=offer.request.period_days),
            tag=offer.request.tag,
        )
        self.credits[credit.credit_id] = credit
        result.fills.append(
            FillEvent(
                at,
                offer.offer_id,
                credit.credit_id,
                amount,
                rate,
                offer.request.rate_type,
                offer.request.period_days,
                (at - offer.created_at).total_seconds(),
                source,
            )
        )
        if offer.remaining <= 0:
            del self.offers[offer.offer_id]

    # --------------------------------------------------------------- decision
    def portfolio(self, at: datetime) -> PortfolioState:
        sym = self.config.currency
        return PortfolioState(
            as_of=at,
            currency=sym,
            available=self.available,
            offers=tuple(o.view(sym) for o in self.offers.values()),
            credits=tuple(c.view(sym) for c in self.credits.values()),
            pending_interest=self.pending,
        )

    def _decide(self, step: int, market: MarketState, result: SimulationResult) -> None:
        state = self.portfolio(market.as_of)
        decision: Decision = self.strategy.decide(market, state)
        decision, rejections = enforce(decision, state, self.constraints)
        result.rejections.extend(r.reason for r in rejections)
        for offer_id in decision.cancel:
            offer = self.offers.pop(offer_id)
            self.available += offer.remaining
            result.offers_cancelled += 1
        for request in decision.place:
            if request.amount > self.available:  # enforce() guarantees this; defensive
                continue
            self.available -= request.amount
            offer = _Offer(
                self._next_id("o"),
                request,
                request.amount,
                market.as_of,
                step + self.scenario.latency_steps,
            )
            self.offers[offer.offer_id] = offer
            result.offers_placed += 1
            result.offered_amount += request.amount

    def _snapshot(self, at: datetime) -> Snapshot:
        offered = sum((o.remaining for o in self.offers.values()), Decimal(0))
        lent = sum((c.amount for c in self.credits.values()), Decimal(0))
        lock = sum(
            (
                c.amount * Decimal(max(0.0, (c.expires_at - at).total_seconds())) / SECONDS_PER_DAY
                for c in self.credits.values()
            ),
            Decimal(0),
        )
        return Snapshot(
            at=at,
            available=self.available,
            offered=offered,
            lent=lent,
            pending_interest=self.pending,
            gross_interest=self.gross,
            fees=self.fees,
            paid_interest=self.paid,
            weighted_remaining_lock_days=(lock / lent) if lent > 0 else Decimal(0),
        )
