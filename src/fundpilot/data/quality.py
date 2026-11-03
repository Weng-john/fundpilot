"""Data quality checks: gaps, staleness, and plausibility of observations.

Checks *report* issues; they never modify or drop source data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Any

from sqlalchemy.orm import Session

from fundpilot.core.models import BookSide, FundingBookSnapshot, FundingStat, FundingTicker
from fundpilot.core.time import utc_now
from fundpilot.data.models import DataQualityIssue
from fundpilot.data.repository import insert_ignore


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class QualityIssue:
    dataset: str
    exchange: str
    symbol: str | None
    kind: str
    severity: Severity
    window_start: datetime | None = None
    window_end: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        payload = json.dumps(
            [
                self.dataset,
                self.exchange,
                self.symbol,
                self.kind,
                self.window_start.isoformat() if self.window_start else None,
                self.window_end.isoformat() if self.window_end else None,
                self.details,
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class QualityLimits:
    max_daily_rate: Decimal = Decimal("0.07")
    min_period_days: int = 2
    max_period_days: int = 120
    staleness: timedelta = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Gap:
    start: datetime  # last observation before the gap
    end: datetime  # first observation after the gap
    missing_intervals: int


def detect_gaps(
    observed: Sequence[datetime], expected_interval: timedelta, tolerance: float = 0.5
) -> list[Gap]:
    """Find holes larger than ``expected_interval * (1 + tolerance)`` in a sorted series."""
    if expected_interval <= timedelta(0):
        raise ValueError("expected_interval must be positive")
    threshold = expected_interval * (1 + tolerance)
    ordered = sorted(observed)
    gaps = []
    for prev, cur in pairwise(ordered):
        delta = cur - prev
        if delta > threshold:
            gaps.append(Gap(prev, cur, int(delta / expected_interval) - 1))
    return gaps


def check_staleness(
    dataset: str,
    exchange: str,
    symbol: str,
    latest: datetime | None,
    limits: QualityLimits,
    now: datetime | None = None,
) -> list[QualityIssue]:
    now = now or utc_now()
    if latest is None:
        return [QualityIssue(dataset, exchange, symbol, "no_data", Severity.WARNING)]
    age = now - latest
    if age > limits.staleness:
        return [
            QualityIssue(
                dataset,
                exchange,
                symbol,
                "stale",
                Severity.WARNING,
                window_start=latest,
                details={"age_seconds": int(age.total_seconds())},
            )
        ]
    return []


def rate_issue(
    dataset: str,
    exchange: str,
    symbol: str,
    name: str,
    value: Decimal | None,
    limits: QualityLimits,
    at: datetime,
) -> list[QualityIssue]:
    if value is None:
        return []
    if value < 0:
        return [
            QualityIssue(
                dataset,
                exchange,
                symbol,
                "negative_rate",
                Severity.ERROR,
                at,
                at,
                {"field": name, "value": str(value)},
            )
        ]
    if value > limits.max_daily_rate:
        return [
            QualityIssue(
                dataset,
                exchange,
                symbol,
                "implausible_rate",
                Severity.WARNING,
                at,
                at,
                {"field": name, "value": str(value)},
            )
        ]
    return []


def validate_ticker(ticker: FundingTicker, limits: QualityLimits) -> list[QualityIssue]:
    ds, ex, sym, at = "funding_tickers", ticker.exchange, ticker.symbol, ticker.observed_at
    issues: list[QualityIssue] = []
    for name in ("frr", "bid", "ask", "last_price", "high", "low"):
        issues += rate_issue(ds, ex, sym, name, getattr(ticker, name), limits, at)
    for name in ("bid_period_days", "ask_period_days"):
        period = getattr(ticker, name)
        if period is not None and not limits.min_period_days <= period <= limits.max_period_days:
            issues.append(
                QualityIssue(
                    ds,
                    ex,
                    sym,
                    "period_out_of_range",
                    Severity.WARNING,
                    at,
                    at,
                    {"field": name, "value": period},
                )
            )
    if ticker.high is not None and ticker.low is not None and ticker.high < ticker.low:
        issues.append(QualityIssue(ds, ex, sym, "high_below_low", Severity.ERROR, at, at))
    return issues


def validate_stat(stat: FundingStat, limits: QualityLimits) -> list[QualityIssue]:
    ds, ex, sym, at = "funding_stats", stat.exchange, stat.symbol, stat.observed_at
    issues = rate_issue(ds, ex, sym, "frr", stat.frr, limits, at)
    if (
        stat.funding_amount is not None
        and stat.funding_amount_used is not None
        and stat.funding_amount_used > stat.funding_amount
    ):
        issues.append(
            QualityIssue(
                ds,
                ex,
                sym,
                "used_exceeds_total",
                Severity.WARNING,
                at,
                at,
                {"used": str(stat.funding_amount_used), "total": str(stat.funding_amount)},
            )
        )
    return issues


def validate_book(book: FundingBookSnapshot, limits: QualityLimits) -> list[QualityIssue]:
    ds, ex, sym, at = "funding_book_snapshots", book.exchange, book.symbol, book.captured_at
    issues: list[QualityIssue] = []
    if not book.levels:
        issues.append(QualityIssue(ds, ex, sym, "empty_book", Severity.WARNING, at, at))
        return issues
    for level in book.levels:
        issues += rate_issue(ds, ex, sym, f"{level.side}_rate", level.rate, limits, at)
        if not limits.min_period_days <= level.period_days <= limits.max_period_days:
            issues.append(
                QualityIssue(
                    ds,
                    ex,
                    sym,
                    "period_out_of_range",
                    Severity.WARNING,
                    at,
                    at,
                    {"value": level.period_days},
                )
            )
    bids = book.side(BookSide.BID)
    asks = book.side(BookSide.ASK)
    if bids and asks:
        best_bid = max(level.rate for level in bids)
        best_ask = min(level.rate for level in asks)
        # Funding levels differ by period, so a crossed top-of-book is a signal, not proof.
        if best_bid > best_ask:
            issues.append(
                QualityIssue(
                    ds,
                    ex,
                    sym,
                    "crossed_book",
                    Severity.INFO,
                    at,
                    at,
                    {"best_bid": str(best_bid), "best_ask": str(best_ask)},
                )
            )
    return issues


def gap_issues(
    dataset: str, exchange: str, symbol: str, gaps: Iterable[Gap], interval: timedelta
) -> list[QualityIssue]:
    return [
        QualityIssue(
            dataset,
            exchange,
            symbol,
            "gap",
            Severity.WARNING,
            g.start,
            g.end,
            {
                "missing_intervals": g.missing_intervals,
                "expected_interval_seconds": int(interval.total_seconds()),
            },
        )
        for g in gaps
    ]


def record_issues(
    session: Session, issues: Iterable[QualityIssue], now: datetime | None = None
) -> int:
    """Persist issues; identical issues (same fingerprint) are stored once."""
    detected_at = now or utc_now()
    rows = [
        {
            "dataset": i.dataset,
            "exchange": i.exchange,
            "symbol": i.symbol,
            "kind": i.kind,
            "severity": i.severity.value,
            "detected_at": detected_at,
            "window_start": i.window_start,
            "window_end": i.window_end,
            "details": i.details,
            "fingerprint": i.fingerprint(),
        }
        for i in issues
    ]
    return insert_ignore(session, DataQualityIssue, rows)
