"""Persist the public WebSocket stream: trades as they arrive, ticker/book periodically."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from fundpilot.data import quality, repository
from fundpilot.data.db import session_scope
from fundpilot.exchanges.bitfinex.ws import BitfinexFundingStream

logger = logging.getLogger(__name__)


async def record(
    stream: BitfinexFundingStream,
    sessions: sessionmaker[Session],
    *,
    flush_every: float = 60.0,
    stop: asyncio.Event | None = None,
    limits: quality.QualityLimits | None = None,
    on_flush: Callable[[dict[str, int]], None] | None = None,
) -> None:
    stop = stop or asyncio.Event()
    limits = limits or quality.QualityLimits()
    task = asyncio.create_task(stream.run(stop))
    try:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), flush_every)
            counts = flush(stream, sessions, limits)
            if on_flush:
                on_flush(counts)
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def flush(
    stream: BitfinexFundingStream, sessions: sessionmaker[Session], limits: quality.QualityLimits
) -> dict[str, int]:
    """Write the current state (idempotent). Returns rows inserted per dataset."""
    st = stream.state
    state = st.market_state()
    counts = {"tickers": 0, "book_levels": 0, "trades": 0}
    if state.stale:
        logger.warning("stream state is stale; skipping ticker/book snapshot")
    with session_scope(sessions) as s:
        counts["trades"] = repository.save_trades(s, list(st.trades.values()))
        if not state.stale:
            if st.ticker is not None:
                ticker = st.ticker.model_copy(update={"observed_at": state.as_of})
                counts["tickers"] = repository.save_tickers(s, [ticker])
                quality.record_issues(s, quality.validate_ticker(ticker, limits))
            if state.book is not None:
                counts["book_levels"] = repository.save_book_snapshot(s, state.book)
                quality.record_issues(s, quality.validate_book(state.book, limits))
    return counts
