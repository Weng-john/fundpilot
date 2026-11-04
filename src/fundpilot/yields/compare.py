"""Net lending yields per venue from stored observations.

Bitfinex is kept as a *reference*: it currently does not accept new registrations from Taiwan
(user note, 2026-11-04), so it is flagged ``available = False`` and excluded from "best venue".
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundpilot.data.models import CexEarnRateRow, DefiReserveSnapshotRow, FundingTradeRow

UNAVAILABLE = {"bitfinex": "reference only: Bitfinex currently does not accept Taiwan users"}


@dataclass(slots=True)
class VenueSeries:
    key: str
    venue: str
    asset: str
    basis: str
    available: bool
    note: str | None
    points: list[tuple[datetime, float]] = field(default_factory=list)  # net APR fractions

    def stats(self) -> dict[str, Any]:
        v = sorted(p for _, p in self.points)
        if not v:
            return {"n": 0}
        return {
            "n": len(v),
            "mean": statistics.mean(v),
            "median": statistics.median(v),
            "p10": v[len(v) // 10],
            "p90": v[9 * len(v) // 10],
            "last": self.points[-1][1],
            "last_at": self.points[-1][0],
        }

    def daily(self) -> list[tuple[str, float]]:
        buckets: dict[str, list[float]] = defaultdict(list)
        for t, v in self.points:
            buckets[t.date().isoformat()].append(v)
        return [(d, statistics.mean(vs)) for d, vs in sorted(buckets.items())]


def collect(
    session: Session,
    start: datetime,
    end: datetime,
    *,
    bitfinex_fee: float = 0.15,
    include_bitfinex: bool = True,
) -> list[VenueSeries]:
    out: list[VenueSeries] = []
    for ccy in ("USDT", "USDC"):
        rows = session.execute(
            select(CexEarnRateRow.observed_at, CexEarnRateRow.lending_rate)
            .where(
                CexEarnRateRow.venue == "okx",
                CexEarnRateRow.ccy == ccy,
                CexEarnRateRow.observed_at >= start,
                CexEarnRateRow.observed_at <= end,
                CexEarnRateRow.lending_rate.is_not(None),
            )
            .order_by(CexEarnRateRow.observed_at)
        ).all()
        out.append(
            VenueSeries(
                f"okx_flexible_{ccy}",
                "okx",
                ccy,
                "OKX Simple Earn Flexible hourly lendingRate (believed net of fee)",
                True,
                None,
                [(t, float(r)) for t, r in rows if r is not None],
            )
        )
    snaps = (
        session.execute(
            select(DefiReserveSnapshotRow)
            .where(
                DefiReserveSnapshotRow.observed_at >= start,
                DefiReserveSnapshotRow.observed_at <= end,
                DefiReserveSnapshotRow.origin == "exchange_api",
            )
            .order_by(DefiReserveSnapshotRow.observed_at)
        )
        .scalars()
        .all()
    )
    aave: dict[str, VenueSeries] = {}
    for x in snaps:
        key = f"aave_{x.chain}_{x.symbol}"
        series = aave.setdefault(
            key,
            VenueSeries(
                key,
                f"aave-{x.chain}",
                x.symbol,
                "Aave V3 supply APR (net of reserve factor)",
                True,
                None,
            ),
        )
        series.points.append((x.observed_at, float(x.supply_apr)))
    out += [aave[k] for k in sorted(aave)]
    if include_bitfinex:
        trades = session.execute(
            select(FundingTradeRow.executed_at, FundingTradeRow.rate)
            .where(
                FundingTradeRow.symbol == "fUST",
                FundingTradeRow.amount < 0,
                FundingTradeRow.period_days == 2,
                FundingTradeRow.executed_at >= start,
                FundingTradeRow.executed_at <= end,
                FundingTradeRow.origin == "exchange_api",
            )
            .order_by(FundingTradeRow.executed_at)
        ).all()
        out.append(
            VenueSeries(
                "bitfinex_fUST_2d",
                "bitfinex",
                "USDT",
                f"executed 2-day loans (funding taken) x365 x(1-{bitfinex_fee}); own fills not "
                "guaranteed",
                False,
                UNAVAILABLE["bitfinex"],
                [(t, float(r) * 365 * (1 - bitfinex_fee)) for t, r in trades],
            )
        )
    return out


def summary(series: list[VenueSeries], min_points: int = 24) -> dict[str, Any]:
    """Stats per venue; the "best" pick needs ``min_points`` observations to avoid ranking a
    venue on a single snapshot."""
    rows = []
    for s in series:
        rows.append(
            {
                "key": s.key,
                "venue": s.venue,
                "asset": s.asset,
                "basis": s.basis,
                "available": s.available,
                "note": s.note,
                **s.stats(),
            }
        )
    candidates = [r for r in rows if r["available"] and r.get("n", 0) >= min_points]
    best = max(candidates, key=lambda r: r["median"], default=None)
    return {
        "unit": "net APR, fraction per year (simple)",
        "venues": rows,
        "best_available_by_median": None if best is None else best["key"],
        "min_points_for_ranking": min_points,
    }
