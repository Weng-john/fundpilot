"""Virtual funding account: the shared execution model for backtests and paper trading.

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fundpilot.backtesting.scenarios import ExecutionScenario, PeriodMatch
from fundpilot.core.models import BookSide
from fundpilot.core.portfolio import CreditView, OfferView, PortfolioState, RateType
from fundpilot.core.rates import SECONDS_PER_DAY
from fundpilot.strategies.base import Decision, MarketState, OfferRequest
from fundpilot.strategies.constraints import FundingConstraints, Rejection, enforce


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


@dataclass(frozen=True, slots=True)
class OfferEvent:
    at: datetime
    offer_id: str
    event: str  # "submitted" | "cancelled" | "partially_filled" | "filled"
    amount: Decimal  # remaining after the event (original amount for "submitted")
    rate_type: RateType
    rate: Decimal | None
    period_days: int


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
    offer_events: list[OfferEvent] = field(default_factory=list)
    payouts: list[tuple[datetime, Decimal]] = field(default_factory=list)
    stale_steps: int = 0
    steps: int = 0
    kind: str = "simulated"


class VirtualFundingAccount:
    """Funding wallet whose fills, accrual, repayments and payouts follow an execution scenario.

    Call :meth:`advance` once per market state (strictly increasing), then optionally
    :meth:`apply` a decision and :meth:`activate` new offers, then :meth:`snapshot`.
    """

    def __init__(
        self,
        scenario: ExecutionScenario,
        config: SimulationConfig,
        constraints: FundingConstraints | None = None,
        strategy_name: str = "",
    ) -> None:
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
        self.log = SimulationResult(strategy_name, scenario, config)
        self.step = -1
        self.market: MarketState | None = None
        self._prev_as_of: datetime | None = None
        self._prev_frr: Decimal | None = None
        self._last_payout: datetime | None = None

    def _next_id(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}{self._ids}"

    # ------------------------------------------------------------------ steps
    def advance(self, market: MarketState) -> None:
        prev_as_of = self._prev_as_of
        if prev_as_of is not None and market.as_of <= prev_as_of:
            raise ValueError("market states must be strictly increasing in time")
        self.step += 1
        result = self.log
        if prev_as_of is None:
            self._last_payout = market.as_of
        else:
            self._accrue(prev_as_of, self._prev_frr, market.as_of)
            self._repayments(prev_as_of, market.as_of, result)
        assert self._last_payout is not None  # noqa: S101 - set on first step
        if market.as_of - self._last_payout >= self.config.payout_interval:
            if self.pending > 0:
                result.payouts.append((market.as_of, self.pending))
            self.available += self.pending
            self.paid += self.pending
            self.pending = Decimal(0)
            self._last_payout = market.as_of
        if market.stale:
            result.stale_steps += 1
        else:
            self._activate(self.step, market, result)
            if prev_as_of is not None:
                self._fill_from_trades(self.step, prev_as_of, market, result)
        self.market = market
        self._prev_as_of = market.as_of
        self._prev_frr = market.frr

    def activate(self) -> None:
        """Activate offers whose latency has elapsed (e.g. zero-latency offers just placed)."""
        if self.market is not None and not self.market.stale:
            self._activate(self.step, self.market, self.log)

    def apply(self, decision: Decision) -> list[Rejection]:
        """Enforce exchange constraints, then execute cancels and new offers at the current step."""
        at = self._now()
        decision, rejections = enforce(decision, self.portfolio(at), self.constraints)
        self.log.rejections.extend(r.reason for r in rejections)
        for offer_id in decision.cancel:
            self.cancel_offer(offer_id)
        for request in decision.place:
            if request.amount <= self.available:  # enforce() guarantees this; defensive
                self._open_offer(request, at)
        return rejections

    def place_offer(self, request: OfferRequest) -> str:
        """Place one offer (used by the paper execution backend). Raises if infeasible."""
        at = self._now()
        decision, rejections = enforce(Decision((request,)), self.portfolio(at), self.constraints)
        if rejections or not decision.place:
            reason = rejections[0].reason if rejections else "rejected"
            raise ValueError(f"offer rejected by exchange constraints: {reason}")
        return self._open_offer(decision.place[0], at)

    def cancel_offer(self, offer_id: str) -> None:
        """Cancel the unfilled remainder of an offer. Credits cannot be cancelled."""
        if offer_id in self.credits:
            raise ValueError(f"{offer_id} is an active credit and cannot be recalled")
        offer = self.offers.pop(offer_id, None)
        if offer is None:
            raise KeyError(f"unknown offer {offer_id}")
        self.available += offer.remaining
        self.log.offers_cancelled += 1
        r = offer.request
        self.log.offer_events.append(
            OfferEvent(
                self._now(),
                offer_id,
                "cancelled",
                offer.remaining,
                r.rate_type,
                r.rate,
                r.period_days,
            )
        )

    def _open_offer(self, request: OfferRequest, at: datetime) -> str:
        self.available -= request.amount
        offer = _Offer(
            self._next_id("o"), request, request.amount, at, self.step + self.scenario.latency_steps
        )
        self.offers[offer.offer_id] = offer
        self.log.offers_placed += 1
        self.log.offered_amount += request.amount
        self.log.offer_events.append(
            OfferEvent(
                at,
                offer.offer_id,
                "submitted",
                request.amount,
                request.rate_type,
                request.rate,
                request.period_days,
            )
        )
        return offer.offer_id

    def _now(self) -> datetime:
        if self.market is None:
            raise RuntimeError("advance() must be called first")
        return self.market.as_of

    def snapshot(self) -> Snapshot:
        if self.market is None:
            raise RuntimeError("no market state yet")
        snap = self._snapshot(self.market.as_of)
        self.log.snapshots.append(snap)
        self.log.steps += 1
        return snap

    # ------------------------------------------------------------- accounting
    def _accrue(self, prev_as_of: datetime, prev_frr: Decimal | None, now: datetime) -> None:
        fee_rate = self.constraints.lender_fee
        for credit in self.credits.values():
            start = max(prev_as_of, credit.opened_at)
            end = min(now, credit.expires_at)
            if end <= start:
                continue
            if credit.rate_type is RateType.FRR and prev_frr is not None and prev_frr > 0:
                credit.rate = prev_frr
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
        trades = [
            t
            for t in market.trades
            if prev < t.executed_at <= market.as_of
            and (t.amount < 0 or self.scenario.count_lender_taker_volume)
        ]
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
        self.log.offer_events.append(
            OfferEvent(
                at,
                offer.offer_id,
                "filled" if offer.remaining <= 0 else "partially_filled",
                offer.remaining,
                offer.request.rate_type,
                offer.request.rate,
                offer.request.period_days,
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


# ---------------------------------------------------------------------------
# Persistence (paper trading restarts)
# ---------------------------------------------------------------------------
STATE_VERSION = 1


def _dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _pdt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _req_state(r: OfferRequest) -> dict[str, Any]:
    return {
        "rate_type": r.rate_type.value,
        "rate": None if r.rate is None else str(r.rate),
        "period_days": r.period_days,
        "amount": str(r.amount),
        "tag": r.tag,
    }


def _req_from(d: dict[str, Any]) -> OfferRequest:
    return OfferRequest(
        RateType(d["rate_type"]),
        None if d["rate"] is None else Decimal(d["rate"]),
        int(d["period_days"]),
        Decimal(d["amount"]),
        d["tag"],
    )


def account_to_state(acct: VirtualFundingAccount) -> dict[str, Any]:
    """JSON-serialisable snapshot of all state needed to resume the account."""
    rng_state = acct._rng.getstate()
    return {
        "version": STATE_VERSION,
        "ids": acct._ids,
        "step": acct.step,
        "available": str(acct.available),
        "pending": str(acct.pending),
        "gross": str(acct.gross),
        "fees": str(acct.fees),
        "paid": str(acct.paid),
        "prev_as_of": _dt(acct._prev_as_of),
        "prev_frr": None if acct._prev_frr is None else str(acct._prev_frr),
        "last_payout": _dt(acct._last_payout),
        "rng": [rng_state[0], list(rng_state[1]), rng_state[2]],
        "offers": [
            {
                "offer_id": o.offer_id,
                "request": _req_state(o.request),
                "remaining": str(o.remaining),
                "created_at": _dt(o.created_at),
                "active_from_step": o.active_from_step,
                "queue_ahead": str(o.queue_ahead),
                "activated": o.activated,
                "activated_at": _dt(o.activated_at),
                "first_fill_at": _dt(o.first_fill_at),
            }
            for o in acct.offers.values()
        ],
        "credits": [
            {
                "credit_id": c.credit_id,
                "offer_id": c.offer_id,
                "rate_type": c.rate_type.value,
                "rate": str(c.rate),
                "period_days": c.period_days,
                "amount": str(c.amount),
                "opened_at": _dt(c.opened_at),
                "expires_at": _dt(c.expires_at),
                "tag": c.tag,
                "gross_interest": str(c.gross_interest),
            }
            for c in acct.credits.values()
        ],
    }


def account_from_state(
    state: dict[str, Any],
    scenario: ExecutionScenario,
    config: SimulationConfig,
    constraints: FundingConstraints | None = None,
    strategy_name: str = "",
) -> VirtualFundingAccount:
    if state.get("version") != STATE_VERSION:
        raise ValueError(f"unsupported account state version {state.get('version')}")
    acct = VirtualFundingAccount(scenario, config, constraints, strategy_name)
    acct._ids = int(state["ids"])
    acct.step = int(state["step"])
    acct.available = Decimal(state["available"])
    acct.pending = Decimal(state["pending"])
    acct.gross = Decimal(state["gross"])
    acct.fees = Decimal(state["fees"])
    acct.paid = Decimal(state["paid"])
    acct._prev_as_of = _pdt(state["prev_as_of"])
    acct._prev_frr = None if state["prev_frr"] is None else Decimal(state["prev_frr"])
    acct._last_payout = _pdt(state["last_payout"])
    v, internal, gauss = state["rng"]
    acct._rng.setstate((v, tuple(internal), gauss))
    for o in state["offers"]:
        created = _pdt(o["created_at"])
        assert created is not None  # noqa: S101
        acct.offers[o["offer_id"]] = _Offer(
            o["offer_id"],
            _req_from(o["request"]),
            Decimal(o["remaining"]),
            created,
            int(o["active_from_step"]),
            Decimal(o["queue_ahead"]),
            bool(o["activated"]),
            _pdt(o["activated_at"]),
            _pdt(o["first_fill_at"]),
        )
    for c in state["credits"]:
        opened, expires = _pdt(c["opened_at"]), _pdt(c["expires_at"])
        assert opened is not None and expires is not None  # noqa: S101
        acct.credits[c["credit_id"]] = _Credit(
            c["credit_id"],
            c["offer_id"],
            RateType(c["rate_type"]),
            Decimal(c["rate"]),
            int(c["period_days"]),
            Decimal(c["amount"]),
            opened,
            expires,
            c["tag"],
            Decimal(c["gross_interest"]),
        )
    return acct
