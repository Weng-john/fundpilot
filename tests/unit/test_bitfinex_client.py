from __future__ import annotations

import json
import random
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from fundpilot.exchanges.bitfinex.client import BitfinexRestClient
from fundpilot.exchanges.errors import (
    ExchangeAPIError,
    ExchangeUnavailableError,
    RateLimitedError,
    ResponseParseError,
)
from fundpilot.exchanges.ratelimit import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], max_retries: int = 3
) -> tuple[BitfinexRestClient, FakeClock]:
    clock = FakeClock()
    limiter = TokenBucket(60, burst=100, clock=clock, sleep=clock.sleep)
    client = BitfinexRestClient(
        "https://example.invalid/v2",
        max_retries=max_retries,
        rate_limiter=limiter,
        rate_limit_cooldown=60.0,
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        rng=random.Random(0),
    )
    return client, clock


def test_success_parses_floats_as_decimal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/ticker/fUST"
        return httpx.Response(200, content=b"[0.1, 0.000123456789012345678]")

    client, _ = make_client(handler)
    body = client.get("ticker/fUST")
    assert body == [Decimal("0.1"), Decimal("0.000123456789012345678")]


def test_none_params_dropped() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    client, _ = make_client(handler)
    client.get("trades/fUST/hist", {"limit": 10, "start": None})
    assert seen == {"limit": "10"}


def test_retries_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="maintenance")
        return httpx.Response(200, json=[1])

    client, clock = make_client(handler)
    assert client.get("platform/status") == [1]
    assert calls["n"] == 3
    assert len(clock.sleeps) == 2


def test_timeout_exhausts_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    client, _ = make_client(handler, max_retries=2)
    with pytest.raises(ExchangeUnavailableError):
        client.get("platform/status")
    assert calls["n"] == 3


def test_param_error_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, content=json.dumps(["error", 10020, "symbol: invalid"]))

    client, _ = make_client(handler)
    with pytest.raises(ExchangeAPIError) as info:
        client.get("ticker/fXXX")
    assert info.value.code == 10020
    assert calls["n"] == 1


def test_rate_limit_triggers_cooldown_and_retry() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, content=json.dumps(["error", 11010, "ratelimit: error"]))
        return httpx.Response(200, json=[1])

    client, clock = make_client(handler)
    assert client.get("platform/status") == [1]
    assert sum(clock.sleeps) >= 60.0


def test_http_429_is_rate_limited_and_raised_when_exhausted() -> None:
    client, _ = make_client(lambda _: httpx.Response(429), max_retries=0)
    with pytest.raises(RateLimitedError):
        client.get("platform/status")


def test_4xx_non_json_raises_parse_error() -> None:
    client, _ = make_client(lambda _: httpx.Response(404, text="<html>"))
    with pytest.raises(ResponseParseError):
        client.get("nope")


def test_token_bucket_throttles() -> None:
    clock = FakeClock()
    bucket = TokenBucket(60, burst=2, clock=clock, sleep=clock.sleep)
    for _ in range(4):
        bucket.acquire()
    # 2 burst tokens free, then 1 token per second
    assert pytest.approx(sum(clock.sleeps), rel=1e-6) == 2.0


def test_token_bucket_cooldown_blocks() -> None:
    clock = FakeClock()
    bucket = TokenBucket(60, burst=5, clock=clock, sleep=clock.sleep)
    bucket.cooldown(30)
    bucket.acquire()
    assert clock.now >= 30
