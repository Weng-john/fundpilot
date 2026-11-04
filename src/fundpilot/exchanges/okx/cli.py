"""``fundpilot okx ...`` (public data only) and ``fundpilot compare-yields``."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fundpilot.core.time import utc_now
from fundpilot.data.db import session_scope
from fundpilot.data.models import CexEarnRateRow
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
    cy = sub.add_parser(
        "collect-yields", help="append OKX Flexible + Aave observations to CSV files (no DB)"
    )
    cy.add_argument("--csv-dir", required=True)
    cy.add_argument("--okx-hours", type=int, default=48, help="OKX history window per run")
    cy.set_defaults(func=cmd_collect_yields)
    iy = sub.add_parser("import-yields", help="load collected CSV files into the database")
    iy.add_argument("--csv-dir", required=True)
    iy.set_defaults(func=lambda st, a: cmd_import_yields(st, a, sessions_factory))


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


def cmd_compare(settings: Any, args: Any, sessions_factory: Any) -> int:
    from fundpilot.yields.compare import collect, summary  # noqa: PLC0415

    end = utc_now()
    start = end - timedelta(days=args.days)
    with sessions_factory(settings) as sessions, session_scope(sessions) as s:
        series = collect(s, start, end, bitfinex_fee=float(args.bitfinex_fee))
    out = summary(series)
    out["window"] = [start, end]
    for v in out["venues"]:
        for k in ("mean", "median", "p10", "p90", "last"):
            if k in v:
                v[k] = round(v[k] * 100, 3)
    out["unit"] = "net APR, % per year (simple)"
    _print(out)
    return 0


def cmd_collect_yields(settings: Any, args: Any) -> int:
    from pathlib import Path  # noqa: PLC0415

    from fundpilot.yields.csvstore import collect_aave, collect_okx  # noqa: PLC0415

    directory = Path(args.csv_dir)
    result: dict[str, Any] = {"okx": collect_okx(directory, args.okx_hours)}
    if settings.aave_rpc_urls:
        result["aave"] = collect_aave(directory, settings.aave_rpc_urls)
    else:
        result["aave"] = "skipped: FUNDPILOT_AAVE_RPC_URLS not set"
    _print(result)
    aave = result["aave"]
    return 1 if isinstance(aave, dict) and aave.get("errors") else 0


def cmd_import_yields(settings: Any, args: Any, sessions_factory: Any) -> int:
    from pathlib import Path  # noqa: PLC0415

    from fundpilot.yields.csvstore import import_dir  # noqa: PLC0415

    with sessions_factory(settings) as sessions, session_scope(sessions) as s:
        _print(import_dir(s, Path(args.csv_dir)))
    return 0
