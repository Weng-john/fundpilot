"""Low-level Bitfinex REST v2 client: transport, rate limiting, retries, error mapping.

Error formats:

* ``["error", <code>, "<message>"]`` - codes 10000 unknown, 10001 generic, 10020 params,
  10100 auth (official SDK; 10020 also observed live for an invalid symbol);
* ``{"error": "ERR_RATE_LIMIT"}`` - rate limited; the IP is blocked for 60 s
  (docs.bitfinex.com/docs/requirements-and-limitations).

Per-endpoint limits (requests/min, docs.bitfinex.com reference pages, checked 2026-11-03):
ticker 90, book 240, candles 30, trades 15, platform/status 30, tickers 30. Funding stats
has no published limit; the documented general minimum (10/min) is used.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

import httpx

from fundpilot.exchanges.errors import (
    ExchangeAPIError,
    ExchangeUnavailableError,
    RateLimitedError,
    ResponseParseError,
)
from fundpilot.exchanges.ratelimit import TokenBucket

logger = logging.getLogger(__name__)

ERR_UNKNOWN = 10000
ERR_GENERIC = 10001
ERR_PARAMS = 10020
ERR_AUTH_FAIL = 10100
ERR_RATE_LIMIT = 11010  # not documented; kept as an extra defensive signal

# Documented per-endpoint limits (requests per minute), matched by path prefix.
ENDPOINT_LIMITS: dict[str, int] = {
    "ticker/": 90,
    "book/": 240,
    "candles/": 30,
    "trades/": 15,
    "platform/status": 30,
    "tickers": 30,
    "funding/stats/": 10,
    "conf/": 10,
}
DEFAULT_LIMIT = 10  # documented general minimum


def default_limiters(
    safety: float = 0.5, sleep: Callable[[float], None] = time.sleep
) -> dict[str, TokenBucket]:
    """One token bucket per endpoint family at ``safety`` x the documented limit."""
    return {
        prefix: TokenBucket(max(1, int(limit * safety)), sleep=sleep)
        for prefix, limit in {**ENDPOINT_LIMITS, "": DEFAULT_LIMIT}.items()
    }


_NON_RETRYABLE_CODES = frozenset({ERR_PARAMS, ERR_AUTH_FAIL})


def _is_error_body(body: Any) -> bool:
    return isinstance(body, list) and len(body) >= 1 and body[0] == "error"


def _is_error_object(body: Any) -> bool:
    return isinstance(body, dict) and "error" in body


def _is_rate_limit(code: Any, message: str) -> bool:
    lowered = message.lower()
    return code == ERR_RATE_LIMIT or "ratelimit" in lowered or "rate_limit" in lowered


class BitfinexRestClient:
    """Synchronous JSON client. Floats are parsed as ``Decimal`` to keep precision."""

    def __init__(
        self,
        base_url: str = "https://api-pub.bitfinex.com/v2",
        *,
        timeout: float = 15.0,
        max_retries: int = 3,
        rate_limiter: TokenBucket | None = None,
        limiters: dict[str, TokenBucket] | None = None,
        rate_limit_cooldown: float = 60.0,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        user_agent: str = "fundpilot/0.1",
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": user_agent},
        )
        self._max_retries = max_retries
        # A single explicit limiter (tests) or one bucket per endpoint family (default).
        if rate_limiter is not None:
            self._limiters = {"": rate_limiter}
        else:
            self._limiters = limiters or default_limiters(sleep=sleep)
        self._cooldown = rate_limit_cooldown
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter only, not security

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BitfinexRestClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _limiter_for(self, path: str) -> TokenBucket:
        clean = path.lstrip("/")
        best = max((p for p in self._limiters if clean.startswith(p)), key=len, default="")
        return self._limiters.get(best) or next(iter(self._limiters.values()))

    def _backoff(self, attempt: int) -> float:
        delay: float = min(self._backoff_cap, self._backoff_base * (2**attempt))
        return delay * (0.5 + self._rng.random() / 2)

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            limiter = self._limiter_for(path)
            limiter.acquire()
            try:
                response = self._client.get(path.lstrip("/"), params=clean)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = ExchangeUnavailableError(f"transport error on {path}: {exc}")
                logger.warning("bitfinex transport error (attempt %d): %s", attempt + 1, exc)
                if attempt < self._max_retries:
                    self._sleep(self._backoff(attempt))
                continue

            body = self._decode(response, path)
            retry_error = self._classify(response.status_code, body, path)
            if retry_error is None:
                return body
            last_error = retry_error
            if attempt < self._max_retries:
                if isinstance(retry_error, RateLimitedError):
                    # the block is per IP, so pause every endpoint family
                    for bucket in self._limiters.values():
                        bucket.cooldown(self._cooldown)
                else:
                    self._sleep(self._backoff(attempt))
        raise last_error or ExchangeUnavailableError(f"request to {path} failed")

    @staticmethod
    def _decode(response: httpx.Response, path: str) -> Any:
        if not response.content:
            return None
        try:
            return response.json(parse_float=Decimal)
        except ValueError as exc:
            if response.status_code >= 500:
                return None
            raise ResponseParseError(f"non-JSON response from {path}: {exc}") from exc

    @staticmethod
    def _classify(status: int, body: Any, path: str) -> Exception | None:
        """Return a *retryable* error, ``None`` on success, or raise a terminal error."""
        if _is_error_object(body):
            message = str(body.get("error"))
            if _is_rate_limit(None, message):
                return RateLimitedError(f"rate limited on {path}: {message}")
            if status >= 500:
                return ExchangeUnavailableError(f"bitfinex error on {path}: {message}")
            raise ExchangeAPIError(f"bitfinex error on {path}: {message}")
        if _is_error_body(body):
            code = body[1] if len(body) > 1 else None
            message = str(body[2]) if len(body) > 2 else ""
            if _is_rate_limit(code, message):
                return RateLimitedError(f"rate limited on {path}: {message}")
            if code in _NON_RETRYABLE_CODES or status < 500:
                raise ExchangeAPIError(f"bitfinex error on {path}: {message}", code=code)
            return ExchangeUnavailableError(f"bitfinex error {code} on {path}: {message}")
        if status == 429:
            return RateLimitedError(f"HTTP 429 on {path}")
        if status >= 500:
            return ExchangeUnavailableError(f"HTTP {status} on {path}")
        if status >= 400:
            raise ExchangeAPIError(f"HTTP {status} on {path}: {body!r}")
        return None
