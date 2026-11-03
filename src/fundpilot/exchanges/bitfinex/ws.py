"""Bitfinex public WebSocket v2 for funding channels, with reconnection.

Message formats follow the official ``bitfinex-api-py`` 4.0.0 SDK:

* events are JSON objects: ``{"event": "info", "version": 2}``, ``{"event": "subscribed",
  "channel": ..., "chanId": ...}``, ``{"event": "info", "code": 20051}`` (server restart:
  reconnect), ``{"event": "error", ...}``;
* channel data are arrays ``[chanId, payload]``; ``[chanId, "hb"]`` is a heartbeat;
* funding book snapshot = list of ``[RATE, PERIOD, COUNT, AMOUNT]``, update = one such row;
* trades snapshot = list of ``[ID, MTS, AMOUNT, RATE, PERIOD]``; updates
  ``[chanId, "fte"|"ftu", row]``;
* ``[chanId, "cs", checksum]`` book checksums (ignored here).

UNVERIFIED (not in the SDK): a book update with ``COUNT == 0`` removes the level; info codes
20060/20061 announce maintenance start/end.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from fundpilot.core.models import BookSide, FundingBookLevel, FundingBookSnapshot, FundingTrade
from fundpilot.core.time import utc_now
from fundpilot.exchanges.bitfinex import parsers
from fundpilot.strategies.base import MarketState

logger = logging.getLogger(__name__)

PUBLIC_WS_URL = "wss://api-pub.bitfinex.com/ws/2"
INFO_RECONNECT = 20051
INFO_MAINTENANCE_START = 20060  # unverified
INFO_MAINTENANCE_END = 20061  # unverified


class ReconnectRequested(Exception):
    """Server asked clients to reconnect, or the connection went silent."""


@dataclass(slots=True)
class FundingStreamState:
    """Pure message handler that maintains ticker, book and recent trades for one symbol."""

    symbol: str
    clock: Callable[[], datetime] = utc_now
    trade_lookback: timedelta = timedelta(hours=24)
    channels: dict[int, str] = field(default_factory=dict)
    book: dict[tuple[str, str, int], FundingBookLevel] = field(default_factory=dict)
    trades: dict[int, FundingTrade] = field(default_factory=dict)
    ticker: Any = None
    last_message_at: datetime | None = None
    last_data_at: dict[str, datetime] = field(default_factory=dict)
    maintenance: bool = False
    book_ready: bool = False

    def reset(self) -> None:
        """Clear channel-dependent state (on reconnect a fresh snapshot arrives)."""
        self.channels.clear()
        self.book.clear()
        self.book_ready = False

    def handle(self, raw: str | bytes) -> None:
        msg = json.loads(raw, parse_float=Decimal)
        self.last_message_at = self.clock()
        if isinstance(msg, dict):
            self._event(msg)
        elif isinstance(msg, list) and len(msg) >= 2:
            self._data(msg)

    def _event(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "subscribed":
            self.channels[int(msg["chanId"])] = str(msg["channel"])
        elif event == "info":
            code = msg.get("code")
            if code == INFO_RECONNECT:
                raise ReconnectRequested("server requested reconnect (20051)")
            if code == INFO_MAINTENANCE_START:
                self.maintenance = True
            elif code == INFO_MAINTENANCE_END:
                self.maintenance = False
            if "version" in msg and msg["version"] != 2:
                raise RuntimeError(f"unsupported websocket API version {msg['version']}")
        elif event == "error":
            logger.error("websocket error event: %s", msg)

    def _data(self, msg: list[Any]) -> None:
        channel = self.channels.get(int(msg[0]))
        payload = msg[1]
        if channel is None or payload == "hb":
            return
        now = self.clock()
        if channel == "book":
            if isinstance(payload, str):  # "cs" checksum
                return
            self._book(payload)
        elif channel == "trades":
            if isinstance(payload, str) and payload in {"fte", "ftu"}:
                self._trades([msg[2]])
            elif isinstance(payload, list):
                self._trades(payload)
        elif channel == "ticker":
            row = payload[0] if payload and isinstance(payload[0], list) else payload
            self.ticker = parsers.parse_funding_ticker(row, symbol=self.symbol, observed_at=now)
        self.last_data_at[channel] = now

    def _book(self, payload: list[Any]) -> None:
        rows = payload if payload and isinstance(payload[0], list) else [payload]
        is_snapshot = bool(payload) and isinstance(payload[0], list)
        if is_snapshot:
            self.book.clear()
        for row in rows:
            rate, period, count, amount = row[0], int(row[1]), int(row[2]), Decimal(row[3])
            side = BookSide.BID if amount < 0 else BookSide.ASK
            key = (side.value, str(Decimal(rate).normalize()), period)
            if count == 0:
                self.book.pop(key, None)
            else:
                self.book[key] = FundingBookLevel(
                    side=side,
                    rate=Decimal(rate),
                    period_days=period,
                    amount=abs(amount),
                    count=count,
                )
        self.book_ready = True

    def _trades(self, rows: list[Any]) -> None:
        for t in parsers.parse_funding_trades(rows, symbol=self.symbol):
            self.trades[t.trade_id] = t
        cutoff = self.clock() - self.trade_lookback
        self.trades = {k: v for k, v in self.trades.items() if v.executed_at > cutoff}

    def snapshot(self) -> FundingBookSnapshot | None:
        if not self.book_ready:
            return None
        return FundingBookSnapshot(
            exchange="bitfinex",
            symbol=self.symbol,
            precision="P0",
            captured_at=self.clock(),
            levels=tuple(self.book.values()),
        )

    def market_state(self, max_staleness: timedelta = timedelta(minutes=2)) -> MarketState:
        now = self.clock()
        stale = (
            self.maintenance
            or self.last_message_at is None
            or now - self.last_message_at > max_staleness
            or not self.book_ready
        )
        trades = tuple(
            sorted(
                (t for t in self.trades.values() if t.executed_at <= now),
                key=lambda t: t.executed_at,
            )
        )
        return MarketState(now, self.symbol, self.ticker, self.snapshot(), trades, stale)


class Connection(Protocol):
    async def send(self, message: str) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def close(self) -> None: ...


Connector = Callable[[str], Awaitable[Connection]]


async def _default_connect(url: str) -> Connection:
    import websockets  # noqa: PLC0415

    conn: Connection = await websockets.connect(url, ping_interval=20, ping_timeout=20)
    return conn


@dataclass(slots=True)
class BitfinexFundingStream:
    """Subscribes to ticker, P0 book and trades for a funding symbol; reconnects on failure."""

    state: FundingStreamState
    url: str = PUBLIC_WS_URL
    book_length: int = 100
    connect: Connector = _default_connect
    silence_timeout: float = 30.0
    backoff_base: float = 1.0
    backoff_cap: float = 60.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    rng: random.Random = field(default_factory=random.Random)
    reconnects: int = 0

    def subscriptions(self) -> list[dict[str, Any]]:
        sym = self.state.symbol
        return [
            {"event": "subscribe", "channel": "ticker", "symbol": sym},
            {
                "event": "subscribe",
                "channel": "book",
                "symbol": sym,
                "prec": "P0",
                "freq": "F0",
                "len": str(self.book_length),
            },
            {"event": "subscribe", "channel": "trades", "symbol": sym},
        ]

    async def run(self, stop: asyncio.Event, max_connections: int | None = None) -> None:
        attempt = 0
        connections = 0
        while not stop.is_set():
            if max_connections is not None and connections >= max_connections:
                return
            connections += 1
            try:
                conn = await self.connect(self.url)
                attempt = 0
                self.state.reset()
                for sub in self.subscriptions():
                    await conn.send(json.dumps(sub))
                await self._read(conn, stop)
                with contextlib.suppress(Exception):
                    await conn.close()
                if stop.is_set():
                    return
            except ReconnectRequested as exc:
                logger.warning("reconnecting: %s", exc)
            except Exception as exc:  # network errors etc.: back off and reconnect
                logger.warning("websocket failure: %s", exc)
                delay = min(self.backoff_cap, self.backoff_base * 2**attempt)
                attempt += 1
                await self.sleep(delay * (0.5 + self.rng.random() / 2))
            self.reconnects += 1

    async def _read(self, conn: Connection, stop: asyncio.Event) -> None:
        iterator = conn.__aiter__()
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(iterator.__anext__(), self.silence_timeout)
            except StopAsyncIteration as exc:
                raise ConnectionError("connection closed by server") from exc
            except TimeoutError as exc:
                raise ReconnectRequested(f"no message for {self.silence_timeout}s") from exc
            self.state.handle(raw)
