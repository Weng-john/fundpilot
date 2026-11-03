"""Periodic market data collection.

Each (job, symbol) pair runs in its own transaction and is recorded in
``ingestion_runs`` so failures are visible and isolated.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from fundpilot.config import Settings
from fundpilot.core.time import utc_now
from fundpilot.data import quality, repository
from fundpilot.data.db import session_scope
from fundpilot.data.models import IngestionRun
from fundpilot.exchanges.base import FundingMarketDataAdapter

logger = logging.getLogger(__name__)

JobFn = Callable[[Session, str], tuple[int, int, list[quality.QualityIssue]]]


@dataclass(slots=True)
class JobResult:
    job: str
    symbol: str
    status: str
    rows_fetched: int = 0
    rows_inserted: int = 0
    issues: list[quality.QualityIssue] = field(default_factory=list)
    error: str | None = None


class MarketDataCollector:
    def __init__(
        self,
        adapter: FundingMarketDataAdapter,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._adapter = adapter
        self._sessions = session_factory
        self._settings = settings
        self._clock = clock
        self._limits = quality.QualityLimits(
            max_daily_rate=settings.max_plausible_daily_rate,
            min_period_days=settings.min_funding_period_days,
            max_period_days=settings.max_funding_period_days,
        )

    # -- jobs ----------------------------------------------------------
    def _ticker(self, session: Session, symbol: str) -> tuple[int, int, list[quality.QualityIssue]]:
        ticker = self._adapter.get_funding_ticker(symbol)
        return (
            1,
            repository.save_tickers(session, [ticker]),
            quality.validate_ticker(ticker, self._limits),
        )

    def _book(self, session: Session, symbol: str) -> tuple[int, int, list[quality.QualityIssue]]:
        book = self._adapter.get_funding_book(
            symbol, self._settings.book_precision, self._settings.book_length
        )
        inserted = repository.save_book_snapshot(session, book)
        return len(book.levels), inserted, quality.validate_book(book, self._limits)

    def _raw_book(
        self, session: Session, symbol: str
    ) -> tuple[int, int, list[quality.QualityIssue]]:
        book = self._adapter.get_funding_raw_book(symbol, self._settings.raw_book_length)
        inserted = repository.save_book_snapshot(session, book)
        return len(book.levels), inserted, quality.validate_book(book, self._limits)

    def _stats(self, session: Session, symbol: str) -> tuple[int, int, list[quality.QualityIssue]]:
        stats = self._adapter.get_funding_stats(symbol, limit=10)
        issues = [i for s in stats for i in quality.validate_stat(s, self._limits)]
        return len(stats), repository.save_funding_stats(session, stats), issues

    def _trades(self, session: Session, symbol: str) -> tuple[int, int, list[quality.QualityIssue]]:
        trades = self._adapter.get_funding_trades(symbol, limit=self._settings.trades_limit)
        issues = [
            i
            for t in trades
            for i in quality.rate_issue(
                "funding_trades", t.exchange, t.symbol, "rate", t.rate, self._limits, t.executed_at
            )
        ]
        return len(trades), repository.save_trades(session, trades), issues

    def jobs(self) -> dict[str, JobFn]:
        return {
            "funding_ticker": self._ticker,
            "funding_book": self._book,
            "funding_raw_book": self._raw_book,
            "funding_stats": self._stats,
            "funding_trades": self._trades,
        }

    # -- orchestration -----------------------------------------------------
    def run_job(self, name: str, fn: JobFn, symbol: str) -> JobResult:
        with session_scope(self._sessions) as session:
            run = IngestionRun(
                job=name,
                exchange=self._adapter.exchange,
                symbol=symbol,
                started_at=self._clock(),
                status="running",
            )
            session.add(run)
            session.flush()
            run_id = run.id

        result = JobResult(job=name, symbol=symbol, status="success")
        try:
            with session_scope(self._sessions) as session:
                fetched, inserted, issues = fn(session, symbol)
                quality.record_issues(session, issues, now=self._clock())
            result.rows_fetched, result.rows_inserted, result.issues = fetched, inserted, issues
        except Exception as exc:  # isolate failures per job; recorded below
            logger.exception("ingestion job %s/%s failed", name, symbol)
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"

        with session_scope(self._sessions) as session:
            run = session.get_one(IngestionRun, run_id)
            run.status = result.status
            run.finished_at = self._clock()
            run.rows_fetched = result.rows_fetched
            run.rows_inserted = result.rows_inserted
            run.error = result.error
        return result

    def _trading_candles(
        self, session: Session, pair: str
    ) -> tuple[int, int, list[quality.QualityIssue]]:
        candles = self._adapter.get_trading_candles(pair, "1h", limit=3)  # type: ignore[attr-defined]
        return len(candles), repository.save_trading_candles(session, candles), []

    def collect_once(self) -> list[JobResult]:
        results = []
        for symbol in self._settings.symbols:
            for name, fn in self.jobs().items():
                results.append(self.run_job(name, fn, symbol))
        if hasattr(self._adapter, "get_trading_candles"):
            for pair in self._settings.volatility_pairs:
                results.append(self.run_job("trading_candles", self._trading_candles, pair))
        return results

    def run_forever(
        self,
        *,
        iterations: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        count = 0
        while iterations is None or count < iterations:
            started = time.monotonic()
            results = self.collect_once()
            failed = [r for r in results if r.status == "failed"]
            logger.info("collection cycle done: %d jobs, %d failed", len(results), len(failed))
            count += 1
            if iterations is not None and count >= iterations:
                break
            elapsed = time.monotonic() - started
            sleep(max(0.0, self._settings.collect_interval_seconds - elapsed))
