from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from fundpilot.exchanges.base import FundingMarketDataAdapter
from fundpilot.exchanges.bitfinex import BitfinexPublicAdapter, BitfinexRestClient
from fundpilot.exchanges.bitfinex.symbols import funding_candle_key, validate_funding_symbol
from fundpilot.exchanges.ratelimit import TokenBucket
from tests.conftest import FIXED_NOW, FIXTURES

ROUTES = {
    "/v2/platform/status": [1],
    "/v2/candles/trade:1h:tBTCUSD/hist": [[1761955200000, 100, 101, 102, 99, 5]],
    "/v2/ticker/tUSTUSD": [0.9998, 1, 1.0001, 1, 0, 0, 0.99995, 1000, 1.001, 0.999],
    "/v2/ticker/fUST": "funding_ticker.json",
    "/v2/book/fUST/P0": "funding_book_p0.json",
    "/v2/book/fUST/R0": "funding_book_r0.json",
    "/v2/funding/stats/fUST/hist": "funding_stats.json",
    "/v2/trades/fUST/hist": "funding_trades.json",
    "/v2/candles/trade:1h:fUST:a30:p2:p30/hist": "funding_candles.json",
}


@pytest.fixture
def requests_seen() -> list[httpx.Request]:
    return []


@pytest.fixture
def adapter(requests_seen: list[httpx.Request]) -> BitfinexPublicAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        target = ROUTES.get(request.url.path)
        if target is None:
            return httpx.Response(500, json=["error", 10020, "unknown route"])
        if isinstance(target, str):
            return httpx.Response(200, content=(FIXTURES / target).read_bytes())
        return httpx.Response(200, json=target)

    client = BitfinexRestClient(
        "https://example.invalid/v2",
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(90, burst=90),
        sleep=lambda _: None,
    )
    return BitfinexPublicAdapter(client, clock=lambda: FIXED_NOW)


def test_satisfies_protocol(adapter: BitfinexPublicAdapter) -> None:
    assert isinstance(adapter, FundingMarketDataAdapter)


def test_endpoints(adapter: BitfinexPublicAdapter, requests_seen: list[httpx.Request]) -> None:
    assert adapter.is_operational()
    assert adapter.get_funding_ticker("fUST").observed_at == FIXED_NOW
    book = adapter.get_funding_book("fUST", "P0", 25)
    assert book.captured_at == FIXED_NOW
    assert requests_seen[-1].url.params["len"] == "25"
    assert len(adapter.get_funding_raw_book("fUST").levels) == 2

    start = datetime(2025, 11, 1, tzinfo=UTC)
    stats = adapter.get_funding_stats("fUST", start=start, limit=2)
    assert requests_seen[-1].url.params["start"] == str(int(start.timestamp() * 1000))
    assert stats[0].frr == Decimal("5.479452e-7") * 365

    adapter.get_funding_trades("fUST", limit=2)
    assert requests_seen[-1].url.params["sort"] == "-1"

    candles = adapter.get_funding_candles("fUST", "1h")
    assert candles[0].candle_key == "trade:1h:fUST:a30:p2:p30"


def test_trading_candles(adapter: BitfinexPublicAdapter) -> None:
    candles = adapter.get_trading_candles("tBTCUSD", "1h", limit=1)
    assert candles[0].close == Decimal(101) and candles[0].timeframe == "1h"
    with pytest.raises(ValueError):
        adapter.get_trading_candles("fUST")
    with pytest.raises(ValueError):
        adapter.get_trading_candles("tBTCUSD", "7m")


def test_last_price(adapter: BitfinexPublicAdapter) -> None:
    assert adapter.get_last_price("tUSTUSD") == Decimal("0.99995")
    with pytest.raises(ValueError):
        adapter.get_last_price("fUST")


def test_argument_validation(adapter: BitfinexPublicAdapter) -> None:
    with pytest.raises(ValueError):
        adapter.get_funding_book("fUST", "R0")
    with pytest.raises(ValueError):
        adapter.get_funding_book("fUST", "P0", 50)
    with pytest.raises(ValueError):
        adapter.get_funding_ticker("tBTCUSD")


def test_symbol_helpers() -> None:
    assert validate_funding_symbol("fUSD") == "fUSD"
    with pytest.raises(ValueError):
        validate_funding_symbol("UST")
    assert funding_candle_key("1D", "fUST", "p30") == "trade:1D:fUST:p30"
    with pytest.raises(ValueError):
        funding_candle_key("2m", "fUST", "p30")
    with pytest.raises(ValueError):
        funding_candle_key("1h", "fUST", "p30;drop")
