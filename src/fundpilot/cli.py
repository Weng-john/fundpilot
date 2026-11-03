"""Command-line interface: ``fundpilot <command>``.

Phase 1 commands only touch public market data and the local database. No
command here can place, modify or cancel exchange orders.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from fundpilot.config import Settings, get_settings
from fundpilot.core.time import utc_now
from fundpilot.data import backfill, quality, repository, retention
from fundpilot.data.db import create_db_engine, make_session_factory, session_scope
from fundpilot.data.ingestion import MarketDataCollector
from fundpilot.data.migrate import upgrade
from fundpilot.exchanges.bitfinex import BitfinexPublicAdapter


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@contextmanager
def _sessions(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(settings.database_url)
    try:
        yield make_session_factory(engine)
    finally:
        engine.dispose()


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_db_upgrade(settings: Settings, _: argparse.Namespace) -> int:
    upgrade(settings.database_url)
    print("database schema is at head")
    return 0


def cmd_collect(settings: Settings, args: argparse.Namespace) -> int:
    adapter = BitfinexPublicAdapter.from_settings(settings)
    with _sessions(settings) as sessions:
        return _collect(MarketDataCollector(adapter, sessions, settings), adapter, args)


def _collect(
    collector: MarketDataCollector, adapter: BitfinexPublicAdapter, args: argparse.Namespace
) -> int:
    try:
        if args.once:
            results = collector.collect_once()
            _print(
                [
                    {
                        "job": r.job,
                        "symbol": r.symbol,
                        "status": r.status,
                        "fetched": r.rows_fetched,
                        "inserted": r.rows_inserted,
                        "issues": len(r.issues),
                        "error": r.error,
                    }
                    for r in results
                ]
            )
            return 0 if all(r.status == "success" for r in results) else 1
        collector.run_forever(iterations=args.iterations)
        return 0
    finally:
        adapter.close()


def cmd_backfill(settings: Settings, args: argparse.Namespace) -> int:
    adapter = BitfinexPublicAdapter.from_settings(settings)
    start, end = _parse_dt(args.start), _parse_dt(args.end) if args.end else utc_now()
    try:
        with _sessions(settings) as sessions:
            if args.dataset == "stats":
                report = backfill.backfill_funding_stats(adapter, sessions, args.symbol, start, end)
            elif args.dataset == "trades":
                report = backfill.backfill_trades(adapter, sessions, args.symbol, start, end)
            else:
                report = backfill.backfill_candles(
                    adapter, sessions, args.symbol, args.timeframe, start, end
                )
    finally:
        adapter.close()
    _print(asdict(report))
    return 0


def cmd_quality(settings: Settings, args: argparse.Namespace) -> int:
    limits = quality.QualityLimits(
        max_daily_rate=settings.max_plausible_daily_rate,
        staleness=timedelta(seconds=settings.staleness_threshold_seconds),
    )
    interval = timedelta(seconds=settings.collect_interval_seconds)
    found: list[quality.QualityIssue] = []
    with _sessions(settings) as sessions, session_scope(sessions) as session:
        for symbol in settings.symbols:
            for dataset in ("funding_tickers", "funding_book_snapshots"):
                latest = repository.latest_timestamp(session, dataset, "bitfinex", symbol)
                found += quality.check_staleness(dataset, "bitfinex", symbol, latest, limits)
                since = utc_now() - timedelta(hours=args.hours)
                ts = repository.timestamps(session, dataset, "bitfinex", symbol, since)
                found += quality.gap_issues(
                    dataset, "bitfinex", symbol, quality.detect_gaps(ts, interval), interval
                )
        stored = quality.record_issues(session, found)
    _print(
        {
            "issues_found": len(found),
            "issues_stored": stored,
            "issues": [
                {
                    "dataset": i.dataset,
                    "symbol": i.symbol,
                    "kind": i.kind,
                    "severity": i.severity.value,
                    "window_start": i.window_start,
                    "window_end": i.window_end,
                    "details": i.details,
                }
                for i in found
            ],
        }
    )
    return 0


def cmd_prune(settings: Settings, args: argparse.Namespace) -> int:
    cutoff = utc_now() - timedelta(days=args.older_than_days)
    with _sessions(settings) as sessions, session_scope(sessions) as session:
        result = retention.prune(session, args.dataset, cutoff, dry_run=not args.confirm)
    _print(asdict(result))
    if not args.confirm:
        print("dry run only - re-run with --confirm to delete", file=sys.stderr)
    return 0


def cmd_status(settings: Settings, _: argparse.Namespace) -> int:
    with _sessions(settings) as sessions, session_scope(sessions) as session:
        counts = repository.row_counts(session)
    _print(
        {
            "execution_mode": settings.execution_mode.value,
            "symbols": settings.symbols,
            "row_counts": counts,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fundpilot", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    db = sub.add_parser("db", help="database management")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("upgrade", help="apply migrations").set_defaults(func=cmd_db_upgrade)

    collect = sub.add_parser("collect", help="collect public funding market data")
    collect.add_argument("--once", action="store_true", help="run a single cycle")
    collect.add_argument("--iterations", type=int, default=None)
    collect.set_defaults(func=cmd_collect)

    bf = sub.add_parser("backfill", help="historical backfill")
    bf.add_argument("dataset", choices=["stats", "trades", "candles"])
    bf.add_argument("--symbol", default="fUST")
    bf.add_argument("--start", required=True, help="ISO-8601, UTC if no offset")
    bf.add_argument("--end", default=None)
    bf.add_argument("--timeframe", default="1h")
    bf.set_defaults(func=cmd_backfill)

    q = sub.add_parser("quality", help="run data quality checks")
    q.add_argument("--hours", type=int, default=24)
    q.set_defaults(func=cmd_quality)

    pr = sub.add_parser("prune", help="apply retention (dry-run unless --confirm)")
    pr.add_argument("dataset", choices=sorted(retention.PRUNABLE))
    pr.add_argument("--older-than-days", type=int, required=True)
    pr.add_argument("--confirm", action="store_true", help="actually delete rows")
    pr.set_defaults(func=cmd_prune)

    sub.add_parser("status", help="show configuration and row counts").set_defaults(func=cmd_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return int(args.func(get_settings(), args))


if __name__ == "__main__":
    raise SystemExit(main())
