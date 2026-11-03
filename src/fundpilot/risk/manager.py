"""Risk manager.

The risk manager never *adds* exposure. It can drop or shrink proposed offers, hold them
for manual approval, add cancels of *unfilled* offers, and block new offers entirely.
It cannot recall active funding credits: a lender's capital returns only when the borrower
repays or the term ends (per Bitfinex support docs as seen via search; see
docs/risk-management.md). Messages say so explicitly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.strategies.base import Decision, MarketState, OfferRequest

CREDITS_NOT_RECALLABLE = (
    "Active funding credits cannot be recalled; they stay lent until the borrower repays "
    "or the term ends."
)


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class RiskAlert:
    code: str
    severity: AlertSeverity
    message: str


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_allocation_fraction: Decimal = Decimal(1)  # (offered + lent) / total capital
    max_period_days: int = 120
    max_single_offer: Decimal | None = None
    min_daily_rate: Decimal = Decimal(0)
    min_liquidity_fraction: Decimal = Decimal(0)  # available / total kept after decision
    max_period_concentration: Decimal = Decimal(1)  # per period (offered + lent) / total
    max_data_age: timedelta = timedelta(minutes=5)
    max_consecutive_api_failures: int = 3
    depeg_threshold: Decimal = Decimal("0.02")  # |price - 1|
    approval_threshold: Decimal | None = None  # offers above this need manual approval
    cancel_unfilled_on_block: bool = True

    def __post_init__(self) -> None:
        for name in (
            "max_allocation_fraction",
            "min_liquidity_fraction",
            "max_period_concentration",
        ):
            if not Decimal(0) <= getattr(self, name) <= Decimal(1):
                raise ValueError(f"{name} must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class HealthStatus:
    as_of: datetime
    platform_operational: bool | None = None  # None = unknown
    consecutive_api_failures: int = 0
    last_market_data_at: datetime | None = None
    stablecoin_price: Decimal | None = None  # e.g. USDt/USD; None = not monitored
    emergency_stop: bool = False
    emergency_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    decision: Decision  # approved cancels and (possibly shrunk) offers
    rejected: tuple[tuple[OfferRequest, str], ...] = ()
    needs_approval: tuple[OfferRequest, ...] = ()
    alerts: tuple[RiskAlert, ...] = ()
    new_offers_blocked: bool = False
    adjustments: tuple[str, ...] = field(default_factory=tuple)


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    # -- health ---------------------------------------------------------------
    def blockers(self, market: MarketState, health: HealthStatus) -> list[RiskAlert]:
        lim = self.limits
        alerts: list[RiskAlert] = []
        if health.emergency_stop:
            alerts.append(
                RiskAlert(
                    "emergency_stop",
                    AlertSeverity.CRITICAL,
                    f"Emergency stop active ({health.emergency_reason or 'no reason given'}). "
                    f"New offers suspended. {CREDITS_NOT_RECALLABLE}",
                )
            )
        if health.platform_operational is False:
            alerts.append(
                RiskAlert(
                    "exchange_maintenance",
                    AlertSeverity.CRITICAL,
                    "Exchange reports it is not operational.",
                )
            )
        if health.consecutive_api_failures >= lim.max_consecutive_api_failures:
            alerts.append(
                RiskAlert(
                    "api_failures",
                    AlertSeverity.CRITICAL,
                    f"{health.consecutive_api_failures} consecutive API failures.",
                )
            )
        age = (
            None
            if health.last_market_data_at is None
            else (health.as_of - health.last_market_data_at)
        )
        if market.stale or age is None or age > lim.max_data_age:
            alerts.append(
                RiskAlert(
                    "stale_market_data",
                    AlertSeverity.WARNING,
                    f"Market data is stale or missing (age={age}).",
                )
            )
        if health.stablecoin_price is not None:
            deviation = abs(health.stablecoin_price - 1)
            if deviation > lim.depeg_threshold:
                alerts.append(
                    RiskAlert(
                        "stablecoin_depeg",
                        AlertSeverity.CRITICAL,
                        f"Stablecoin price {health.stablecoin_price} deviates {deviation} from 1.",
                    )
                )
        return alerts

    # -- evaluation -----------------------------------------------------------
    def evaluate(
        self,
        decision: Decision,
        portfolio: PortfolioState,
        market: MarketState,
        health: HealthStatus,
    ) -> RiskVerdict:
        lim = self.limits
        blockers = self.blockers(market, health)
        known = {o.offer_id for o in portfolio.offers}
        cancels = [c for c in dict.fromkeys(decision.cancel) if c in known]
        if blockers:
            blocked = tuple((r, "new offers blocked: " + blockers[0].code) for r in decision.place)
            if lim.cancel_unfilled_on_block:
                cancels = list(dict.fromkeys([*cancels, *known]))
            return RiskVerdict(
                Decision((), tuple(cancels), decision.notes),
                blocked,
                (),
                tuple(blockers),
                new_offers_blocked=True,
            )

        total = portfolio.total
        cancelled_amount = sum(
            (o.amount for o in portfolio.offers if o.offer_id in cancels), Decimal(0)
        )
        available = portfolio.available + cancelled_amount
        deployed = portfolio.offered + portfolio.lent - cancelled_amount
        by_period: dict[int, Decimal] = {}
        for o in portfolio.offers:
            if o.offer_id not in cancels:
                by_period[o.period_days] = by_period.get(o.period_days, Decimal(0)) + o.amount
        for c in portfolio.credits:
            by_period[c.period_days] = by_period.get(c.period_days, Decimal(0)) + c.amount

        accepted: list[OfferRequest] = []
        rejected: list[tuple[OfferRequest, str]] = []
        approval: list[OfferRequest] = []
        notes: list[str] = []
        for req in decision.place:
            reason = self._static_reject(req, market)
            if reason:
                rejected.append((req, reason))
                continue
            caps = {
                "requested": req.amount,
                "single_offer": lim.max_single_offer,
                "allocation": lim.max_allocation_fraction * total - deployed,
                "liquidity": available - lim.min_liquidity_fraction * total,
                "concentration": lim.max_period_concentration * total
                - by_period.get(req.period_days, Decimal(0)),
                "available": available,
            }
            binding = min((v, k) for k, v in caps.items() if v is not None)
            amount = max(Decimal(0), binding[0])
            if amount <= 0:
                rejected.append((req, f"no room under {binding[1]} limit"))
                continue
            if amount < req.amount:
                notes.append(
                    f"offer {req.period_days}d shrunk {req.amount} -> {amount} ({binding[1]} limit)"
                )
            sized = replace(req, amount=amount)
            if lim.approval_threshold is not None and amount > lim.approval_threshold:
                approval.append(sized)
                continue
            accepted.append(sized)
            available -= amount
            deployed += amount
            by_period[req.period_days] = by_period.get(req.period_days, Decimal(0)) + amount
        return RiskVerdict(
            Decision(tuple(accepted), tuple(cancels), decision.notes),
            tuple(rejected),
            tuple(approval),
            (),
            False,
            tuple(notes),
        )

    def _static_reject(self, req: OfferRequest, market: MarketState) -> str | None:
        lim = self.limits
        if req.period_days > lim.max_period_days:
            return f"period {req.period_days}d exceeds max {lim.max_period_days}d"
        if req.rate_type is RateType.FIXED:
            if req.rate is None or req.rate < lim.min_daily_rate:
                return f"rate {req.rate} below minimum {lim.min_daily_rate}"
        elif lim.min_daily_rate > 0 and (market.frr is None or market.frr < lim.min_daily_rate):
            return f"current FRR {market.frr} below minimum {lim.min_daily_rate}"
        return None

    def as_gate(
        self, health: HealthStatus | None = None
    ) -> Callable[[Decision, PortfolioState, MarketState], Decision]:
        """Adapter for :class:`FundingSimulator` so backtests can run the same risk layer.

        Health defaults to "all systems normal" with data as fresh as the market state.
        Offers needing approval are treated as not approved in backtests.
        """

        def gate(decision: Decision, portfolio: PortfolioState, market: MarketState) -> Decision:
            h = health or HealthStatus(
                as_of=market.as_of, platform_operational=True, last_market_data_at=market.as_of
            )
            return self.evaluate(decision, portfolio, market, h).decision

        return gate
