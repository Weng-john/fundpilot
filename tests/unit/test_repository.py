from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.core.models import DataOrigin, FundingStat
from fundpilot.data import repository
from fundpilot.data.db import session_scope
from fundpilot.data.models import FundingBookLevelRow, FundingStatRow, FundingTradeRow
from fundpilot.exchanges.bitfinex import parsers
from tests.conftest import FIXED_NOW, load_fixture


def _stats() -> list[FundingStat]:
    return parsers.parse_funding_stats(
        load_fixture("funding_stats.json"), symbol="fUST", frr_scale=Decimal(365)
    )


def test_stats_insert_is_idempotent(sessions: sessionmaker[Session]) -> None:
    with session_scope(sessions) as s:
        assert repository.save_funding_stats(s, _stats()) == 2
    with session_scope(sessions) as s:
        assert repository.save_funding_stats(s, _stats()) == 0
        assert len(s.scalars(select(FundingStatRow)).all()) == 2


def test_decimal_and_utc_roundtrip(sessions: sessionmaker[Session]) -> None:
    with session_scope(sessions) as s:
        repository.save_funding_stats(s, _stats())
    with session_scope(sessions) as s:
        row = s.scalars(select(FundingStatRow).order_by(FundingStatRow.observed_at.desc())).first()
        assert row is not None
        assert row.funding_amount == Decimal("512000000.12345678")
        assert row.frr_raw == Decimal("5.479452e-7")
        assert row.observed_at.tzinfo is not None
        assert row.observed_at.utcoffset() == timedelta(0)
        assert row.origin == DataOrigin.EXCHANGE_API.value


def test_trades_duplicate_within_batch(sessions: sessionmaker[Session]) -> None:
    trades = parsers.parse_funding_trades(load_fixture("funding_trades.json"), symbol="fUST")
    with session_scope(sessions) as s:
        assert repository.save_trades(s, trades + trades) == 2
        assert s.scalars(
            select(FundingTradeRow.amount).order_by(FundingTradeRow.trade_id)
        ).all() == [
            Decimal(1000),
            Decimal("-500.5"),
        ]


def test_book_snapshot_idempotent_with_levels(sessions: sessionmaker[Session]) -> None:
    book = parsers.parse_funding_book(
        load_fixture("funding_book_p0.json"), symbol="fUST", precision="P0", captured_at=FIXED_NOW
    )
    with session_scope(sessions) as s:
        assert repository.save_book_snapshot(s, book) == 4
    with session_scope(sessions) as s:
        assert repository.save_book_snapshot(s, book) == 0
        assert len(s.scalars(select(FundingBookLevelRow)).all()) == 4


def test_latest_timestamp_and_counts(sessions: sessionmaker[Session]) -> None:
    with session_scope(sessions) as s:
        assert repository.latest_timestamp(s, "funding_stats", "bitfinex", "fUST") is None
        repository.save_funding_stats(s, _stats())
        latest = repository.latest_timestamp(s, "funding_stats", "bitfinex", "fUST")
        assert latest == max(x.observed_at for x in _stats())
        assert repository.row_counts(s)["funding_stats"] == 2
        assert len(repository.timestamps(s, "funding_stats", "bitfinex", "fUST")) == 2
