"""``fundpilot okx ...`` (public data only) and ``fundpilot compare-yields``."""

from __future__ import annotations

import json
import statistics
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from fundpilot.core.time import utc_now
from fundpilot.data.db import session_scope
from fundpilot.data.models import CexEarnRateRow, DefiReserveSnapshotRow, FundingTradeRow
from fundpilot.data.repository import insert_ignore
from fundpilot.exchanges.okx.earn import OkxEarnClient


def add_parser(sub: Any, sessions_factory: Any) -> None:
    okx = sub.add_parser("okx", help="OKX Simple Earn Flexible public rates (read-only)")
    o_sub = okx.add_subparsers(dest="okx_command", required=True)
    s = o_sub.add_parser("summary", help="current public borrow-rate summary")
    s.add_argument("--ccy", action="append", default=None)
    s.set_defaults(func=lambda st, a: cmd_summary(a))
    b = o_sub.add_parser("backfill", help="store hourly lending/borrow rates")
    b.add_argument("--ccy", action="append", default=None)
    b.add_argument("--days", type=int, default=90)
    b.set_defaults(func=lambda st, a: cmd_backfill(st, a, sessions_factory))
    c = sub.add_parser(
        "compare-yields", help="net lending yields: Bitfinex funding vs OKX Flexible vs Aave"
    )
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--bitfinex-fee", type=Decimal, default=Decimal("0.15"))
    c.set_defaults(func=lambda st, a: cmd_compare(st, a, sessions_factory))


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_summary(args: Any) -> int:
    client = OkxEarnClient()
    try:
        _print([client.summary(c).model_dump() for c in (args.ccy or ["USDT", "USDC"])])
    finally:
        client.close()
    print("note: summary rates are BORROWING rates; lenders earn `lendingRate` (see backfill)")
    return 0


def cmd_backfill(settings: Any, args: Any, sessions_factory: Any) -> int:
    client = OkxEarnClient()
    start = utc_now() - timedelta(days=args.days)
    out = {}
    try:
        with sessions_factory(settings) as sessions:
            for ccy in args.ccy or ["USDT", "USDC"]:
                rows = client.backfill(ccy, start)
                with session_scope(sessions) as s:
                    n = insert_ignore(
                        s,
                        CexEarnRateRow,
                        [r.model_dump() | {"origin": r.origin.value} for r in rows],
                    )
                out[ccy] = {
                    "fetched": len(rows),
                    "inserted": n,
                    "first": rows[0].observed_at if rows else None,
                    "last": rows[-1].observed_at if rows else None,
                }
    finally:
        client.close()
    _print(out)
    return 0


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    v = sorted(values)
    return {
        "n": len(v),
        "mean": round(statistics.mean(v) * 100, 3),
        "median": round(statistics.median(v) * 100, 3),
        "p10": round(v[len(v) // 10] * 100, 3),
        "p90": round(v[9 * len(v) // 10] * 100, 3),
    }


def cmd_compare(settings: Any, args: Any, sessions_factory: Any) -> int:
    end = utc_now()
    start = end - timedelta(days=args.days)
    fee = float(args.bitfinex_fee)
    out: dict[str, Any] = {"window": [start, end], "unit": "% per year, simple", "venues": {}}
    with sessions_factory(settings) as sessions, session_scope(sessions) as s:
        trades = (
            s.execute(
                select(FundingTradeRow.rate).where(
                    FundingTradeRow.symbol == "fUST",
                    FundingTradeRow.amount < 0,
                    FundingTradeRow.period_days == 2,
                    FundingTradeRow.executed_at >= start,
                    FundingTradeRow.origin == "exchange_api",
                )
            )
            .scalars()
            .all()
        )
        out["venues"]["bitfinex_fUST_2d_taken"] = {
            "basis": f"rates of executed 2-day loans (funding taken) x365 x(1-{fee}); "
            "fills for our own offers are NOT guaranteed",
            **_stats([float(r) * 365 * (1 - fee) for r in trades]),
        }
        for ccy in ("USDT", "USDC"):
            rows = (
                s.execute(
                    select(CexEarnRateRow.lending_rate).where(
                        CexEarnRateRow.venue == "okx",
                        CexEarnRateRow.ccy == ccy,
                        CexEarnRateRow.observed_at >= start,
                        CexEarnRateRow.lending_rate.is_not(None),
                    )
                )
                .scalars()
                .all()
            )
            out["venues"][f"okx_flexible_{ccy}"] = {
                "basis": "hourly lendingRate (believed net of OKX's 15 %)",
                **_stats([float(r) for r in rows if r is not None]),
            }
        snaps = (
            s.execute(
                select(DefiReserveSnapshotRow).where(DefiReserveSnapshotRow.observed_at >= start)
            )
            .scalars()
            .all()
        )
        by_key: dict[str, list[float]] = {}
        for x in snaps:
            by_key.setdefault(f"aave_{x.chain}_{x.symbol}", []).append(float(x.supply_apr))
        for k, v in sorted(by_key.items()):
            out["venues"][k] = {
                "basis": "supply APR snapshots (net of reserve factor)",
                **_stats(v),
            }
    _print(out)
    return 0
