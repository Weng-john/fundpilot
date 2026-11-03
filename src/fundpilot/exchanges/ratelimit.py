"""Thread-safe token-bucket rate limiter with an injectable clock."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        rate_per_minute: int,
        *,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self._capacity = float(burst if burst is not None else max(1, rate_per_minute // 6))
        self._refill_per_second = rate_per_minute / 60.0
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)
        self._updated = now

    def acquire(self) -> float:
        """Block until a token is available. Returns total seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                if now < self._blocked_until:
                    delay = self._blocked_until - now
                else:
                    self._refill(now)
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return waited
                    delay = (1.0 - self._tokens) / self._refill_per_second
            self._sleep(delay)
            waited += delay

    def cooldown(self, seconds: float) -> None:
        """Block all callers for ``seconds`` (e.g. after an exchange rate-limit error)."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)
            self._tokens = 0.0
