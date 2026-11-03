from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from fundpilot.backtesting.feed import FeedConfig, MarketFeed, load_feed
from fundpilot.backtesting.synthetic import SyntheticConfig, generate
from fundpilot.core.models import DataOrigin
from fundpilot.data import repository
from fundpilot.data.db import session_scope
from fundpilot.exchanges.bitfinex import parsers
from tests.conftest import FIXED_NOW, load_fixture
from tests.helpers import book, ticker, trade


def _cfg(**kw: object) -> FeedConfig:
    base = dict(
        exchange="bitfinex",
        symbol="fUST",
        start=FIXED_NOW,
        end=FIXED_NOW + timedelta(minutes=10),
        step=timedelta(minutes=1),
        origins=(DataOrigin.SYNTHETIC,),
    )
    base.update(kw)
    return FeedConfig(**base)  # type: ignore[arg-type]


def test_point_in_time_no_lookahead() -> None:
    t0 = FIXED_NOW
    feed = MarketFeed(
        _cfg(),
        [ticker(t0, "0.0001"), ticker(t0 + timedelta(minutes=5), "0.0009")],
        [book(t0), book(t0 + timedelta(minutes=5))],
        [trade(t0 + timedelta(minutes=m, seconds=30), "0.0002", "10") for m in range(10)],
    )
    for state in feed:
        assert state.ticker is not None and state.ticker.observed_at <= state.as_of
        assert all(t.executed_at <= state.as_of for t in state.trades)
        assert state.book is not None and state.book.captured_at <= state.as_of
    s4 = feed.state_at(t0 + timedelta(minutes=4, seconds=59))
    assert s4.frr == Decimal("0.0001")
    assert feed.state_at(t0 + timedelta(minutes=5)).frr == Decimal("0.0009")


def test_staleness_flag() -> None:
    feed = MarketFeed(
        _cfg(max_staleness=timedelta(minutes=2)), [ticker(FIXED_NOW)], [book(FIXED_NOW)], []
    )
    states = list(feed)
    assert not states[0].stale and not states[2].stale
    assert states[3].stale
    assert MarketFeed(_cfg(), [], [], []).state_at(FIXED_NOW).stale


def test_trade_lookback_window() -> None:
    old = trade(FIXED_NOW - timedelta(hours=2), "0.0002", "1")
    recent = trade(FIXED_NOW - timedelta(minutes=10), "0.0002", "1")
    feed = MarketFeed(_cfg(trade_lookback=timedelta(hours=1)), [], [], [old, recent])
    assert feed.state_at(FIXED_NOW).trades == (recent,)


def test_load_feed_excludes_synthetic_by_default(sessions: sessionmaker[Session]) -> None:
    tickers, books, trades = generate(
        SyntheticConfig(start=FIXED_NOW, days=1, step=timedelta(minutes=10))
    )
    real_ticker = parsers.parse_funding_ticker(
        load_fixture("funding_ticker.json"),
        symbol="fUST",
        observed_at=FIXED_NOW + timedelta(seconds=1),
    )
    with session_scope(sessions) as s:
        repository.save_tickers(s, [*tickers, real_ticker])
        repository.save_trades(s, trades)
        for b in books:
            repository.save_book_snapshot(s, b)
    window = dict(start=FIXED_NOW, end=FIXED_NOW + timedelta(hours=2))
    with session_scope(sessions) as s:
        real_only = load_feed(s, _cfg(origins=(DataOrigin.EXCHANGE_API,), **window))
        both = load_feed(s, _cfg(origins=(DataOrigin.EXCHANGE_API, DataOrigin.SYNTHETIC), **window))
    assert real_only.provenance.tickers == 1
    assert real_only.provenance.books == 0 and real_only.provenance.trades == 0
    assert both.provenance.tickers > 1 and both.provenance.books > 0
    state = both.state_at(FIXED_NOW + timedelta(minutes=30))
    assert state.book is not None and len(state.book.levels) == 20


def test_synthetic_is_tagged_and_deterministic() -> None:
    a = generate(SyntheticConfig(start=FIXED_NOW, days=1, step=timedelta(hours=1), seed=5))
    b = generate(SyntheticConfig(start=FIXED_NOW, days=1, step=timedelta(hours=1), seed=5))
    assert a == b
    assert all(x.origin is DataOrigin.SYNTHETIC for group in a for x in group)
