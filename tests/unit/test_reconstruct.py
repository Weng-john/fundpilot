from __future__ import annotations

import dataclasses
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.backtesting.experiment import ExperimentConfig, run_experiment
from fundpilot.backtesting.feed import FeedConfig, load_feed
from fundpilot.backtesting.reconstruct import tickers_from_funding_stats
from fundpilot.core.models import DataOrigin, FundingStat
from fundpilot.data import repository
from fundpilot.data.db import session_scope
from fundpilot.data.models import FundingTickerRow
from tests.conftest import FIXED_NOW
from tests.helpers import trade


def _stats(hours: int) -> list[FundingStat]:
    return [
        FundingStat(
            exchange="bitfinex",
            symbol="fUST",
            observed_at=FIXED_NOW + timedelta(hours=h),
            frr_raw=Decimal("6.9e-7"),
            frr=Decimal("6.9e-7") * 365,
            avg_period_days=None,
            funding_amount=None,
            funding_amount_used=None,
            funding_below_threshold=None,
        )
        for h in range(hours)
    ]


def test_reconstructed_tickers_are_tagged_and_idempotent(sessions: sessionmaker[Session]) -> None:
    with session_scope(sessions) as s:
        repository.save_funding_stats(s, _stats(5))
        assert (
            tickers_from_funding_stats(
                s,
                exchange="bitfinex",
                symbol="fUST",
                start=FIXED_NOW,
                end=FIXED_NOW + timedelta(days=1),
            )
            == 5
        )
        assert (
            tickers_from_funding_stats(
                s,
                exchange="bitfinex",
                symbol="fUST",
                start=FIXED_NOW,
                end=FIXED_NOW + timedelta(days=1),
            )
            == 0
        )
        rows = s.scalars(select(FundingTickerRow)).all()
    assert {r.origin for r in rows} == {"reconstructed"}
    assert all(r.frr == Decimal("6.9e-7") * 365 and r.bid is None for r in rows)


def test_bookless_feed_and_labelled_report(sessions: sessionmaker[Session]) -> None:
    trades = [
        trade(FIXED_NOW + timedelta(minutes=10 * i, seconds=30), "0.0003", "-5000", 2)
        for i in range(60)
    ]
    with session_scope(sessions) as s:
        repository.save_funding_stats(s, _stats(12))
        repository.save_trades(
            s, [t.model_copy(update={"origin": DataOrigin.EXCHANGE_API}) for t in trades]
        )
        tickers_from_funding_stats(
            s,
            exchange="bitfinex",
            symbol="fUST",
            start=FIXED_NOW,
            end=FIXED_NOW + timedelta(days=1),
        )
    start, end = FIXED_NOW + timedelta(minutes=5), FIXED_NOW + timedelta(hours=9)
    strict = FeedConfig(
        exchange="bitfinex",
        symbol="fUST",
        step=timedelta(minutes=10),
        origins=(DataOrigin.EXCHANGE_API, DataOrigin.RECONSTRUCTED),
        max_staleness=timedelta(hours=2),
        start=start,
        end=end,
    )
    with session_scope(sessions) as s:
        assert all(st.stale for st in load_feed(s, strict))  # no book -> stale by default
        bookless = load_feed(s, dataclasses.replace(strict, require_book=False))
        real_only = load_feed(
            s,
            FeedConfig(
                exchange="bitfinex",
                symbol="fUST",
                step=timedelta(minutes=10),
                max_staleness=timedelta(hours=2),
                require_book=False,
                start=start,
                end=end,
            ),
        )
    states = list(bookless)
    assert not any(st.stale for st in states) and states[0].book is None
    assert all(st.stale for st in real_only)  # reconstructed rows excluded by default

    cfg = ExperimentConfig.from_dict(
        {
            "name": "recon",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step_seconds": 600,
            "scenarios": ["neutral"],
            "allow_reconstructed": True,
            "require_book": False,
            "max_staleness_seconds": 7200,
            "strategies": [
                {"id": "frr", "type": "frr"},
                {"id": "fixed", "type": "fixed_rate", "rate": "0.0002"},
            ],
        }
    )
    report = run_experiment(cfg, states, bookless.provenance, now=FIXED_NOW)
    assert report.data_label == "RECONSTRUCTED"
    assert "RECONSTRUCTED" in report.to_json()["warning"]
    fixed = next(r for r in report.runs if r.strategy_id == "fixed")
    assert fixed.metrics.gross_interest > 0  # real-style taken-funding trades fill offers
