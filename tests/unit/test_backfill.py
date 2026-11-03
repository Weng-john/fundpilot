from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.core.models import FundingTrade
from fundpilot.core.time import to_ms
from fundpilot.data import backfill
from tests.conftest import FIXED_NOW
from tests.fakes import FakeAdapter, make_trades


def _fetcher(
    rows: list[int], limit: int, calls: list[tuple[int, int]]
) -> Callable[[int, int], list[int]]:
    def fetch(start: int, end: int) -> list[int]:
        calls.append((start, end))
        hits = sorted((r for r in rows if start <= r <= end), reverse=True)
        return hits[:limit]

    return fetch


def test_paginate_backward_covers_all_rows_once_deduplicated() -> None:
    rows = list(range(0, 1000, 7))
    calls: list[tuple[int, int]] = []
    pages = list(backfill.paginate_backward(_fetcher(rows, 25, calls), lambda x: x, 0, 999, 25))
    seen = {x for page in pages for x in page}
    assert seen == set(rows)
    # cursor moves strictly backwards
    ends = [end for _, end in calls]
    assert ends == sorted(ends, reverse=True)


def test_paginate_boundary_millisecond_not_skipped() -> None:
    # 3 rows share ts=50 straddling a page boundary.
    rows = [100, 90, 80, 50, 50, 50, 10]
    calls: list[tuple[int, int]] = []

    def fetch(start: int, end: int) -> list[int]:
        calls.append((start, end))
        return sorted((r for r in rows if start <= r <= end), reverse=True)[:4]

    out = [x for page in backfill.paginate_backward(fetch, lambda x: x, 0, 100, 4) for x in page]
    assert out.count(50) >= 3  # duplicates allowed, loss not
    assert 10 in out


def test_paginate_full_page_same_ms_warns() -> None:
    rows = [5] * 10 + [1]
    warnings: list[str] = []

    def fetch(start: int, end: int) -> list[int]:
        return sorted((r for r in rows if start <= r <= end), reverse=True)[:4]

    out = [
        x
        for page in backfill.paginate_backward(fetch, lambda x: x, 0, 5, 4, warnings=warnings)
        for x in page
    ]
    assert 1 in out
    assert warnings and "may be missing" in warnings[0]


def test_paginate_handles_oldest_first_endpoint() -> None:
    rows = list(range(0, 500, 3))

    def fetch(start: int, end: int) -> list[int]:
        return sorted(r for r in rows if start <= r <= end)[:20]  # ascending, like no-sort APIs

    out = {x for page in backfill.paginate_backward(fetch, lambda x: x, 0, 499, 20) for x in page}
    assert out == set(rows)


def test_paginate_rejects_inverted_range() -> None:
    with pytest.raises(ValueError):
        list(backfill.paginate_backward(lambda s, e: [], lambda x: x, 10, 0, 5))


def test_backfill_trades_into_db(sessions: sessionmaker[Session]) -> None:
    trades = make_trades(55, FIXED_NOW - timedelta(hours=1), timedelta(minutes=1))
    adapter = FakeAdapter(trades=trades)
    report = backfill.backfill_trades(
        adapter, sessions, "fUST", FIXED_NOW - timedelta(days=1), FIXED_NOW, page_limit=10
    )
    assert report.rows_inserted == 55
    assert report.warnings == []
    assert report.oldest == min(t.executed_at for t in trades)
    # rerun is idempotent
    again = backfill.backfill_trades(
        adapter, sessions, "fUST", FIXED_NOW - timedelta(days=1), FIXED_NOW, page_limit=10
    )
    assert again.rows_inserted == 0


def test_no_lookahead_beyond_end(sessions: sessionmaker[Session]) -> None:
    trades: list[FundingTrade] = make_trades(10, FIXED_NOW, timedelta(minutes=1))
    adapter = FakeAdapter(trades=trades)
    end = FIXED_NOW + timedelta(minutes=4)
    report = backfill.backfill_trades(adapter, sessions, "fUST", FIXED_NOW, end, page_limit=3)
    assert report.newest is not None and to_ms(report.newest) <= to_ms(end)
    assert report.rows_inserted == 5
