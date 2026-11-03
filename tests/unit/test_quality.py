from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.core.models import BookSide, FundingBookLevel, FundingBookSnapshot
from fundpilot.data import quality
from fundpilot.data.db import session_scope
from fundpilot.data.models import DataQualityIssue
from fundpilot.exchanges.bitfinex import parsers
from tests.conftest import FIXED_NOW, load_fixture

LIMITS = quality.QualityLimits()


def test_detect_gaps() -> None:
    minute = timedelta(minutes=1)
    ts = [FIXED_NOW + minute * i for i in (0, 1, 2, 6, 7)]
    gaps = quality.detect_gaps(ts, minute)
    assert len(gaps) == 1
    assert gaps[0].start == ts[2] and gaps[0].end == ts[3]
    assert gaps[0].missing_intervals == 3


def test_detect_gaps_tolerance_and_unsorted() -> None:
    minute = timedelta(minutes=1)
    ts = [FIXED_NOW + timedelta(seconds=s) for s in (85, 0)]  # 85s < 1.5 * 60s
    assert quality.detect_gaps(ts, minute) == []
    with pytest.raises(ValueError):
        quality.detect_gaps(ts, timedelta(0))


def test_staleness() -> None:
    assert quality.check_staleness("x", "bitfinex", "fUST", None, LIMITS)[0].kind == "no_data"
    fresh = quality.check_staleness("x", "bitfinex", "fUST", FIXED_NOW, LIMITS, now=FIXED_NOW)
    assert fresh == []
    stale = quality.check_staleness(
        "x", "bitfinex", "fUST", FIXED_NOW - timedelta(hours=1), LIMITS, now=FIXED_NOW
    )
    assert stale[0].kind == "stale"
    assert stale[0].details["age_seconds"] == 3600


def test_ticker_validation_passes_for_fixture() -> None:
    ticker = parsers.parse_funding_ticker(
        load_fixture("funding_ticker.json"), symbol="fUST", observed_at=FIXED_NOW
    )
    assert quality.validate_ticker(ticker, LIMITS) == []


def test_ticker_validation_flags_problems() -> None:
    raw = load_fixture("funding_ticker.json")
    raw[0] = Decimal("-0.001")  # negative frr
    raw[2] = 365  # bid period out of range
    raw[11], raw[12] = Decimal("0.0001"), Decimal("0.0002")  # high < low
    ticker = parsers.parse_funding_ticker(raw, symbol="fUST", observed_at=FIXED_NOW)
    kinds = {i.kind for i in quality.validate_ticker(ticker, LIMITS)}
    assert kinds == {"negative_rate", "period_out_of_range", "high_below_low"}


def test_stat_validation() -> None:
    raw = load_fixture("funding_stats.json")
    raw[0][8] = raw[0][7] + 1  # used > total
    stats = parsers.parse_funding_stats(raw, symbol="fUST", frr_scale=Decimal(365))
    assert [i.kind for i in quality.validate_stat(stats[0], LIMITS)] == ["used_exceeds_total"]


def _book(levels: list[FundingBookLevel]) -> FundingBookSnapshot:
    return FundingBookSnapshot(
        exchange="bitfinex",
        symbol="fUST",
        precision="P0",
        captured_at=FIXED_NOW,
        levels=tuple(levels),
    )


def test_book_validation() -> None:
    assert quality.validate_book(_book([]), LIMITS)[0].kind == "empty_book"
    crossed = _book(
        [
            FundingBookLevel(
                side=BookSide.BID, rate=Decimal("0.0003"), period_days=2, amount=Decimal(1)
            ),
            FundingBookLevel(
                side=BookSide.ASK, rate=Decimal("0.0002"), period_days=2, amount=Decimal(1)
            ),
            FundingBookLevel(
                side=BookSide.ASK, rate=Decimal("0.5"), period_days=2, amount=Decimal(1)
            ),
        ]
    )
    kinds = [i.kind for i in quality.validate_book(crossed, LIMITS)]
    assert "crossed_book" in kinds and "implausible_rate" in kinds


def test_record_issues_deduplicates(sessions: sessionmaker[Session]) -> None:
    issues = quality.check_staleness("x", "bitfinex", "fUST", None, LIMITS)
    with session_scope(sessions) as s:
        assert quality.record_issues(s, issues, now=FIXED_NOW) == 1
        assert quality.record_issues(s, issues, now=FIXED_NOW) == 0
        assert len(s.scalars(select(DataQualityIssue)).all()) == 1
