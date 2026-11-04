"""Execution assumptions.

Public data does not reveal whether a given offer would have been filled. Every
fill model is therefore an *assumption*; results must be reported per scenario.
None of the presets below is calibrated against observed fills.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class PeriodMatch(StrEnum):
    EXACT = "exact"  # trade period must equal offer period
    UP_TO = "up_to"  # trade period <= offer period (offer period treated as a maximum)


@dataclass(frozen=True, slots=True)
class ExecutionScenario:
    name: str
    # A public trade at rate R can fill our offer at rate r if R > r (strict) or R >= r.
    strict_rate: bool
    period_match: PeriodMatch
    # Share of resting ask depth at *exactly* our rate assumed to be ahead of us in the queue.
    same_rate_queue_fraction: Decimal
    # Share of eligible traded volume (after the queue ahead) that we capture.
    participation: Decimal
    # Share of resting bid size at/above our rate we capture at placement (immediate match).
    bid_cross_fraction: Decimal
    # Daily hazard of a borrower repaying early (Poisson); 0 disables early repayment.
    early_repay_hazard_per_day: Decimal
    # Decision-to-live latency in simulation steps.
    latency_steps: int = 1
    # Funding trade AMOUNT is positive when the taker *provided* funding (hit a borrower bid) and
    # negative when the taker *took* funding (docs: ws-public-trades). Only taken funding can fill
    # a resting lender offer; counting provided-funding volume too is an optimistic assumption.
    count_lender_taker_volume: bool = False

    def __post_init__(self) -> None:
        for name in ("same_rate_queue_fraction", "participation", "bid_cross_fraction"):
            value = getattr(self, name)
            if not Decimal(0) <= value <= Decimal(1):
                raise ValueError(f"{name} must be within [0, 1]")
        if self.early_repay_hazard_per_day < 0 or self.latency_steps < 0:
            raise ValueError("hazard and latency must be non-negative")


CONSERVATIVE = ExecutionScenario(
    name="conservative",
    strict_rate=True,
    period_match=PeriodMatch.EXACT,
    same_rate_queue_fraction=Decimal(1),
    participation=Decimal("0.1"),
    bid_cross_fraction=Decimal("0.25"),
    early_repay_hazard_per_day=Decimal("0.5"),
    latency_steps=2,
)

NEUTRAL = ExecutionScenario(
    name="neutral",
    strict_rate=False,
    period_match=PeriodMatch.EXACT,
    same_rate_queue_fraction=Decimal("0.5"),
    participation=Decimal("0.3"),
    bid_cross_fraction=Decimal("0.5"),
    early_repay_hazard_per_day=Decimal("0.25"),
    latency_steps=1,
)

OPTIMISTIC = ExecutionScenario(
    name="optimistic",
    strict_rate=False,
    period_match=PeriodMatch.UP_TO,
    same_rate_queue_fraction=Decimal(0),
    participation=Decimal("0.6"),
    bid_cross_fraction=Decimal(1),
    early_repay_hazard_per_day=Decimal("0.1"),
    latency_steps=0,
    count_lender_taker_volume=True,
)

PRESETS: dict[str, ExecutionScenario] = {s.name: s for s in (CONSERVATIVE, NEUTRAL, OPTIMISTIC)}
