"""Performance metrics for simulated results.

Every figure produced here is SIMULATED under an explicit execution scenario.
Definitions (time-weighted over snapshot intervals, left-point rule):

* gross/net annualised yield = interest / average equity * 365 / days (simple)
* net APY = (equity_end / equity_start) ** (365 / days) - 1 (includes simulated
  reinvestment of paid interest; an extrapolation, not a forecast)
* utilisation = average lent / average equity; idle ratio = average available / equity
* free capital = available + unfilled offers (cancellable); locked = active credits
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
from itertools import pairwise
from statistics import median

from fundpilot.backtesting.simulator import SimulationResult

_YEAR = Decimal(365)


@dataclass(frozen=True, slots=True)
class Metrics:
    days: Decimal
    initial_equity: Decimal
    final_equity: Decimal
    average_equity: Decimal
    gross_interest: Decimal
    fees: Decimal
    net_interest: Decimal  # accrued, net of fees (paid + pending)
    paid_interest: Decimal
    operating_costs: Decimal
    gross_annualized_yield: Decimal
    net_annualized_yield: Decimal
    net_annualized_yield_after_costs: Decimal
    net_apy: Decimal | None
    capital_utilization: Decimal
    idle_capital_ratio: Decimal
    offered_unfilled_ratio: Decimal
    min_free_capital_ratio: Decimal
    avg_remaining_lock_days: Decimal
    max_drawdown: Decimal
    fill_ratio: Decimal  # filled amount / offered amount
    median_time_to_first_fill_seconds: float | None
    mean_time_to_fill_seconds: float | None
    offers_placed: int
    offers_cancelled: int
    early_repayments: int
    stale_steps: int
    steps: int

    def as_dict(self) -> dict[str, object]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


def compute_metrics(result: SimulationResult) -> Metrics:
    snaps = result.snapshots
    if len(snaps) < 2:
        raise ValueError("need at least two snapshots to compute metrics")
    with localcontext() as ctx:
        ctx.prec = 40
        total_seconds = Decimal((snaps[-1].at - snaps[0].at).total_seconds())
        days = total_seconds / Decimal(86_400)
        w_equity = w_lent = w_avail = w_offered = w_lock = Decimal(0)
        min_free = Decimal(1)
        peak = snaps[0].equity
        max_dd = Decimal(0)
        for a, b in pairwise(snaps):
            dt = Decimal((b.at - a.at).total_seconds())
            eq = a.equity
            w_equity += eq * dt
            w_lent += a.lent * dt
            w_avail += a.available * dt
            w_offered += a.offered * dt
            w_lock += a.weighted_remaining_lock_days * a.lent * dt
        for s in snaps:
            if s.equity > 0:
                min_free = min(min_free, (s.available + s.offered) / s.equity)
            peak = max(peak, s.equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - s.equity) / peak)

        avg_equity = w_equity / total_seconds
        last = snaps[-1]
        net = last.gross_interest - last.fees
        costs = result.config.operating_cost_per_day * days
        annual = _YEAR / days

        def yld(x: Decimal) -> Decimal:
            return x / avg_equity * annual if avg_equity > 0 else Decimal(0)

        initial = snaps[0].equity
        apy = None
        if initial > 0 and last.equity > 0:
            apy = (last.equity / initial) ** annual - 1

        first_fill: dict[str, float] = {}
        for f in result.fills:
            first_fill.setdefault(f.offer_id, f.seconds_since_offer)
        weighted = [f.seconds_since_offer * float(f.amount) for f in result.fills]
        filled = sum((f.amount for f in result.fills), Decimal(0))

        return Metrics(
            days=days,
            initial_equity=initial,
            final_equity=last.equity,
            average_equity=avg_equity,
            gross_interest=last.gross_interest,
            fees=last.fees,
            net_interest=net,
            paid_interest=last.paid_interest,
            operating_costs=costs,
            gross_annualized_yield=yld(last.gross_interest),
            net_annualized_yield=yld(net),
            net_annualized_yield_after_costs=yld(net - costs),
            net_apy=apy,
            capital_utilization=w_lent / w_equity if w_equity else Decimal(0),
            idle_capital_ratio=w_avail / w_equity if w_equity else Decimal(0),
            offered_unfilled_ratio=w_offered / w_equity if w_equity else Decimal(0),
            min_free_capital_ratio=min_free,
            avg_remaining_lock_days=w_lock / w_lent if w_lent else Decimal(0),
            max_drawdown=max_dd,
            fill_ratio=filled / result.offered_amount if result.offered_amount else Decimal(0),
            median_time_to_first_fill_seconds=median(first_fill.values()) if first_fill else None,
            mean_time_to_fill_seconds=(sum(weighted) / float(filled)) if filled else None,
            offers_placed=result.offers_placed,
            offers_cancelled=result.offers_cancelled,
            early_repayments=sum(1 for c in result.closes if c.reason == "early_repayment"),
            stale_steps=result.stale_steps,
            steps=result.steps,
        )


def excess_return(candidate: Metrics, baseline: Metrics) -> Decimal:
    """Difference in net annualised yield after costs (candidate - baseline)."""
    return candidate.net_annualized_yield_after_costs - baseline.net_annualized_yield_after_costs
