from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.config import Settings
from fundpilot.data.db import session_scope
from fundpilot.data.ingestion import MarketDataCollector
from fundpilot.data.models import DataQualityIssue, FundingTickerRow, IngestionRun
from tests.conftest import FIXED_NOW
from tests.fakes import FakeAdapter, make_trades


def _settings() -> Settings:
    return Settings(symbols=["fUST"], database_url="sqlite://", _env_file=None)


def test_collect_once_success_and_idempotent(sessions: sessionmaker[Session]) -> None:
    adapter = FakeAdapter(trades=make_trades(3, FIXED_NOW, timedelta(seconds=1)))
    collector = MarketDataCollector(adapter, sessions, _settings(), clock=lambda: FIXED_NOW)
    results = collector.collect_once()
    assert {r.job for r in results} == {
        "funding_ticker",
        "funding_book",
        "funding_raw_book",
        "funding_stats",
        "funding_trades",
    }
    assert all(r.status == "success" for r in results)
    again = collector.collect_once()
    assert all(r.rows_inserted == 0 for r in again)
    with session_scope(sessions) as s:
        runs = s.scalars(select(IngestionRun)).all()
        assert len(runs) == 10
        assert all(r.status == "success" and r.finished_at is not None for r in runs)
        assert len(s.scalars(select(FundingTickerRow)).all()) == 1


def test_failure_is_isolated_and_recorded(sessions: sessionmaker[Session]) -> None:
    adapter = FakeAdapter(fail={"book"})
    collector = MarketDataCollector(adapter, sessions, _settings(), clock=lambda: FIXED_NOW)
    results = {r.job: r for r in collector.collect_once()}
    assert results["funding_book"].status == "failed"
    assert "book failed" in (results["funding_book"].error or "")
    assert results["funding_ticker"].status == "success"
    with session_scope(sessions) as s:
        failed = s.scalars(select(IngestionRun).where(IngestionRun.status == "failed")).all()
        assert [r.job for r in failed] == ["funding_book"]


def test_quality_issues_recorded(sessions: sessionmaker[Session]) -> None:
    # FakeAdapter reports a stats FRR of 365/day, far above the plausibility limit.
    collector = MarketDataCollector(FakeAdapter(), sessions, _settings(), clock=lambda: FIXED_NOW)
    collector.collect_once()
    with session_scope(sessions) as s:
        kinds = s.scalars(select(DataQualityIssue.kind)).all()
        assert "implausible_rate" in kinds


def test_run_forever_respects_iterations(sessions: sessionmaker[Session]) -> None:
    sleeps: list[float] = []
    collector = MarketDataCollector(FakeAdapter(), sessions, _settings(), clock=lambda: FIXED_NOW)
    collector.run_forever(iterations=2, sleep=sleeps.append)
    assert len(sleeps) == 1
