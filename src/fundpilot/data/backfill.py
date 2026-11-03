"""Historical backfill via windowed pagination.

Pages are requested inside a ``[lo, hi]`` window that shrinks from the end
(newest-first responses) or from the start (oldest-first responses). Window
edges are *inclusive* so rows sharing a boundary millisecond are not skipped;
duplicates are absorbed by unique constraints. If an entire full page shares
one millisecond the window skips 1 ms and a warning is reported, because rows
may be lost.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from fundpilot.core.time import ensure_utc, from_ms, to_ms
from fundpilot.data import repository
from fundpilot.data.db import session_scope
from fundpilot.exchanges.base import FundingMarketDataAdapter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackfillReport:
    dataset: str
    symbol: str
    pages: int = 0
    rows_fetched: int = 0
    rows_inserted: int = 0
    warnings: list[str] = field(default_factory=list)
    oldest: datetime | None = None
    newest: datetime | None = None


def paginate_backward[T](
    fetch: Callable[[int, int], Sequence[T]],
    key_ms: Callable[[T], int],
    start_ms: int,
    end_ms: int,
    page_limit: int,
    *,
    max_pages: int = 10_000,
    warnings: list[str] | None = None,
) -> Iterator[list[T]]:
    """Yield pages of rows with ``start_ms <= key <= end_ms``.

    Prefers newest-first paging, but adapts if the endpoint returns rows oldest-first
    (e.g. an endpoint without a ``sort`` parameter): the window then advances forwards.
    Boundaries are inclusive so rows sharing a boundary millisecond are re-fetched rather
    than skipped; callers deduplicate.
    """
    if start_ms > end_ms:
        raise ValueError("start must not be after end")

    def warn(message: str) -> None:
        logger.warning(message)
        if warnings is not None:
            warnings.append(message)

    lo, hi = start_ms, end_ms
    for _ in range(max_pages):
        if lo > hi:
            return
        page = list(fetch(lo, hi))
        if not page:
            return
        yield [row for row in page if lo <= key_ms(row) <= hi]
        if len(page) < page_limit:
            return
        keys = [key_ms(row) for row in page]
        oldest, newest = min(keys), max(keys)
        ascending = keys[0] < keys[-1]
        if oldest == newest:
            warn(
                f"full page of {len(page)} rows shares timestamp {oldest}; "
                "skipping 1 ms - rows at this timestamp may be missing"
            )
            if ascending or oldest == lo:
                lo = oldest + 1
            else:
                hi = oldest - 1
        elif ascending:
            lo = newest
        else:
            hi = oldest
    warn(f"max_pages={max_pages} reached before the range was exhausted")


def _run[T](
    sessions: sessionmaker[Session],
    report: BackfillReport,
    pages: Iterator[list[T]],
    save: Callable[[Session, list[T]], int],
    ts: Callable[[T], datetime],
) -> BackfillReport:
    for page in pages:
        report.pages += 1
        report.rows_fetched += len(page)
        if not page:
            continue
        with session_scope(sessions) as session:
            report.rows_inserted += save(session, page)
        times = [ts(row) for row in page]
        report.oldest = min([*times, report.oldest] if report.oldest else times)
        report.newest = max([*times, report.newest] if report.newest else times)
    return report


def backfill_funding_stats(
    adapter: FundingMarketDataAdapter,
    sessions: sessionmaker[Session],
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    page_limit: int = 250,
) -> BackfillReport:
    report = BackfillReport("funding_stats", symbol)
    pages = paginate_backward(
        lambda s, e: adapter.get_funding_stats(
            symbol, start=from_ms(s), end=from_ms(e), limit=page_limit
        ),
        lambda row: to_ms(row.observed_at),
        to_ms(ensure_utc(start)),
        to_ms(ensure_utc(end)),
        page_limit,
        warnings=report.warnings,
    )
    return _run(sessions, report, pages, repository.save_funding_stats, lambda r: r.observed_at)


def backfill_trades(
    adapter: FundingMarketDataAdapter,
    sessions: sessionmaker[Session],
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    page_limit: int = 1000,
) -> BackfillReport:
    report = BackfillReport("funding_trades", symbol)
    pages = paginate_backward(
        lambda s, e: adapter.get_funding_trades(
            symbol, start=from_ms(s), end=from_ms(e), limit=page_limit
        ),
        lambda row: to_ms(row.executed_at),
        to_ms(ensure_utc(start)),
        to_ms(ensure_utc(end)),
        page_limit,
        warnings=report.warnings,
    )
    return _run(sessions, report, pages, repository.save_trades, lambda r: r.executed_at)


def backfill_candles(
    adapter: FundingMarketDataAdapter,
    sessions: sessionmaker[Session],
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    page_limit: int = 1000,
) -> BackfillReport:
    report = BackfillReport(f"funding_candles:{timeframe}", symbol)
    pages = paginate_backward(
        lambda s, e: adapter.get_funding_candles(
            symbol, timeframe, start=from_ms(s), end=from_ms(e), limit=page_limit
        ),
        lambda row: to_ms(row.open_time),
        to_ms(ensure_utc(start)),
        to_ms(ensure_utc(end)),
        page_limit,
        warnings=report.warnings,
    )
    return _run(sessions, report, pages, repository.save_candles, lambda r: r.open_time)
