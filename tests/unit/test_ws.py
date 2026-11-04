from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.core.models import BookSide
from fundpilot.data import repository
from fundpilot.data.db import session_scope
from fundpilot.data.quality import QualityLimits
from fundpilot.data.streaming import flush
from fundpilot.exchanges.bitfinex.ws import (
    BitfinexFundingStream,
    FundingStreamState,
    ReconnectRequested,
)
from tests.conftest import FIXED_NOW

MTS = int(FIXED_NOW.timestamp() * 1000)
TICKER = [
    0.0002,
    0.00019,
    30,
    1000,
    0.00021,
    2,
    500,
    0,
    0,
    0.0002,
    100,
    0.0003,
    0.0001,
    None,
    None,
    50,
]


class Clock:
    def __init__(self) -> None:
        self.now = FIXED_NOW

    def __call__(self) -> datetime:
        return self.now


def _subscribed() -> list[str]:
    return [
        json.dumps({"event": "info", "version": 2}),
        json.dumps({"event": "subscribed", "channel": "ticker", "chanId": 1}),
        json.dumps({"event": "subscribed", "channel": "book", "chanId": 2}),
        json.dumps({"event": "subscribed", "channel": "trades", "chanId": 3}),
    ]


def test_state_builds_book_trades_and_ticker() -> None:
    clock = Clock()
    st = FundingStreamState("fUST", clock=clock)
    for m in _subscribed():
        st.handle(m)
    st.handle(json.dumps([1, TICKER]))
    st.handle(json.dumps([2, [[0.0002, 2, 3, 1000], [0.00021, 7, 1, 500], [0.00019, 30, 2, -800]]]))
    st.handle(json.dumps([2, [0.00022, 2, 1, 300]]))  # add level
    st.handle(json.dumps([2, [0.00021, 7, 0, 1]]))  # count 0 -> remove ask level
    st.handle(json.dumps([2, "hb"]))
    st.handle(json.dumps([2, "cs", 12345]))
    st.handle(json.dumps([3, [[10, MTS - 1000, 100, 0.0002, 2], [11, MTS - 500, -50, 0.00021, 2]]]))
    st.handle(json.dumps([3, "fte", [12, MTS, 25, 0.00022, 2]]))
    st.handle(json.dumps([3, "ftu", [12, MTS, 25, 0.00022, 2]]))  # duplicate id: no double count
    state = st.market_state()
    assert not state.stale
    assert state.frr == Decimal("0.0002")
    assert state.book is not None
    asks = sorted(lv.rate for lv in state.book.side(BookSide.ASK))
    assert asks == [Decimal("0.0002"), Decimal("0.00022")]
    assert [lv.amount for lv in state.book.side(BookSide.BID)] == [Decimal(800)]
    assert [t.trade_id for t in state.trades] == [10, 11, 12]


def test_snapshot_replaces_book_and_staleness() -> None:
    clock = Clock()
    st = FundingStreamState("fUST", clock=clock)
    for m in _subscribed():
        st.handle(m)
    assert st.market_state().stale  # no book yet
    st.handle(json.dumps([2, [[0.0002, 2, 1, 1000]]]))
    st.handle(json.dumps([2, [[0.0003, 2, 1, 1000]]]))  # new snapshot
    assert [lv.rate for lv in st.snapshot().levels] == [Decimal("0.0003")]  # type: ignore[union-attr]
    clock.now += timedelta(minutes=5)
    assert st.market_state().stale  # silent too long


def test_info_events() -> None:
    st = FundingStreamState("fUST")
    st.handle(json.dumps({"event": "info", "code": 20060}))
    assert st.maintenance
    with pytest.raises(ReconnectRequested):  # docs: re-subscribe after maintenance
        st.handle(json.dumps({"event": "info", "code": 20061}))
    assert not st.maintenance
    st.handle(json.dumps({"event": "info", "version": 2, "platform": {"status": 0}}))
    assert st.maintenance and st.market_state().stale
    st.handle(json.dumps({"event": "info", "version": 2, "platform": {"status": 1}}))
    assert not st.maintenance
    with pytest.raises(ReconnectRequested):
        st.handle(json.dumps({"event": "info", "code": 20051}))
    with pytest.raises(RuntimeError):
        st.handle(json.dumps({"event": "info", "version": 3}))


class FakeConn:
    def __init__(self, messages: list[str], hang: bool = False) -> None:
        self.messages = messages
        self.sent: list[dict[str, object]] = []
        self.hang = hang
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self) -> AsyncIterator[str]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[str]:
        for m in self.messages:
            yield m
        if self.hang:
            await asyncio.sleep(3600)

    async def close(self) -> None:
        self.closed = True


def test_stream_reconnects_and_resubscribes() -> None:
    conns: list[FakeConn | Exception] = [
        ConnectionRefusedError("down"),  # first attempt fails
        FakeConn([*_subscribed(), json.dumps({"event": "info", "code": 20051})]),
        FakeConn([*_subscribed(), json.dumps([2, [[0.0002, 2, 1, 1000]]])], hang=True),
    ]
    made: list[FakeConn] = []
    sleeps: list[float] = []

    async def connect(url: str) -> FakeConn:
        item = conns.pop(0)
        if isinstance(item, Exception):
            raise item
        made.append(item)
        return item

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    stream = BitfinexFundingStream(
        FundingStreamState("fUST"),
        connect=connect,
        sleep=fake_sleep,
        silence_timeout=0.05,
    )

    async def main() -> None:
        await stream.run(asyncio.Event(), max_connections=3)

    asyncio.run(main())
    # back-off after the failed connect; >= 3 s pause before each requested/silent reconnect
    # (docs: at most 20 connections per minute to api-pub)
    assert len(sleeps) == 3 and sleeps[1:] == [3.0, 3.0]
    assert stream.reconnects == 3
    assert all(len(c.sent) == 3 for c in made)  # resubscribed each time
    channels = [s["channel"] for s in made[0].sent]
    assert channels == ["ticker", "book", "trades"]
    assert made[1].sent[1]["prec"] == "P0"


def test_flush_persists_stream_state(sessions: sessionmaker[Session]) -> None:
    clock = Clock()
    st = FundingStreamState("fUST", clock=clock)
    for m in _subscribed():
        st.handle(m)
    st.handle(json.dumps([1, TICKER]))
    st.handle(json.dumps([2, [[0.0002, 2, 3, 1000], [0.00019, 30, 2, -800]]]))
    st.handle(json.dumps([3, [[10, MTS - 1000, 100, 0.0002, 2]]]))
    stream = BitfinexFundingStream(st)
    first = flush(stream, sessions, QualityLimits())
    assert first == {"tickers": 1, "book_levels": 2, "trades": 1}
    again = flush(stream, sessions, QualityLimits())  # idempotent
    assert again == {"tickers": 0, "book_levels": 0, "trades": 0}
    with session_scope(sessions) as s:
        assert repository.row_counts(s)["funding_trades"] == 1
