"""PostgreSQL integration tests.

Run only when ``FUNDPILOT_TEST_DATABASE_URL`` points at a **disposable** database:
the schema is dropped (alembic downgrade to base) before and after the tests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.config import Settings
from fundpilot.data import repository, retention
from fundpilot.data.db import create_db_engine, make_session_factory, session_scope
from fundpilot.data.ingestion import MarketDataCollector
from fundpilot.data.migrate import downgrade, upgrade
from fundpilot.data.models import Base, FundingBookLevelRow, FundingStatRow
from fundpilot.exchanges.bitfinex import parsers
from tests.conftest import FIXED_NOW, load_fixture
from tests.fakes import FakeAdapter, make_trades

URL = os.environ.get("FUNDPILOT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="FUNDPILOT_TEST_DATABASE_URL not set"),
]


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    assert URL is not None
    downgrade(URL, "base")
    upgrade(URL)
    engine = create_db_engine(URL)
    yield engine
    engine.dispose()
    downgrade(URL, "base")


@pytest.fixture
def pg_sessions(pg_engine: Engine) -> Iterator[sessionmaker[Session]]:
    yield make_session_factory(pg_engine)
    with pg_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


def test_schema_matches_models(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def test_numeric_precision_and_idempotency(pg_sessions: sessionmaker[Session]) -> None:
    stats = parsers.parse_funding_stats(
        load_fixture("funding_stats.json"), symbol="fUST", frr_scale=Decimal(365)
    )
    with session_scope(pg_sessions) as s:
        assert repository.save_funding_stats(s, stats) == 2
        assert repository.save_funding_stats(s, stats) == 0
    with session_scope(pg_sessions) as s:
        row = s.scalars(select(FundingStatRow).order_by(FundingStatRow.observed_at.desc())).first()
        assert row is not None
        assert row.funding_amount == Decimal("512000000.12345678")
        assert row.frr_raw == Decimal("5.479452E-7")
        assert row.observed_at.utcoffset() == timedelta(0)


def test_check_constraint_and_cascade(pg_sessions: sessionmaker[Session]) -> None:
    book = parsers.parse_funding_book(
        load_fixture("funding_book_p0.json"), symbol="fUST", precision="P0", captured_at=FIXED_NOW
    )
    with session_scope(pg_sessions) as s:
        assert repository.save_book_snapshot(s, book) == 4
    with pytest.raises(IntegrityError), session_scope(pg_sessions) as s:
        level = s.scalars(select(FundingBookLevelRow)).first()
        assert level is not None
        level.amount = Decimal(-1)


def test_collector_against_postgres(pg_sessions: sessionmaker[Session]) -> None:
    settings = Settings(symbols=["fUST"], _env_file=None)
    adapter = FakeAdapter(trades=make_trades(5, FIXED_NOW, timedelta(seconds=1)))
    results = MarketDataCollector(adapter, pg_sessions, settings).collect_once()
    assert all(r.status == "success" for r in results)


def test_prune_counts_on_postgres(pg_sessions: sessionmaker[Session]) -> None:
    with session_scope(pg_sessions) as s:
        repository.save_trades(
            s, make_trades(10, FIXED_NOW - timedelta(days=10), timedelta(days=1))
        )
    with session_scope(pg_sessions) as s:
        result = retention.prune(s, "funding_trades", FIXED_NOW - timedelta(days=5), dry_run=False)
        assert (result.matched, result.deleted) == (5, 5)


def test_paper_engine_on_postgres(pg_sessions: sessionmaker[Session]) -> None:
    from tests.unit.test_paper import Clock, _engine  # noqa: PLC0415

    clock = Clock()
    engine, _ = _engine(pg_sessions, clock)
    for _ in range(4):
        engine.step()
        clock.tick()
    resumed, _ = _engine(pg_sessions, clock)
    assert resumed.account.available == engine.account.available
    assert len(resumed.account.credits) == len(engine.account.credits)
