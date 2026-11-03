"""Exchange-agnostic error hierarchy."""

from __future__ import annotations


class ExchangeError(Exception):
    """Base class for exchange adapter failures."""


class ExchangeUnavailableError(ExchangeError):
    """Network failure, timeout, or 5xx after exhausting retries."""


class RateLimitedError(ExchangeError):
    """Exchange rejected the request because of rate limiting."""


class ExchangeAPIError(ExchangeError):
    """Exchange returned an explicit, non-retryable error."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class ResponseParseError(ExchangeError):
    """Response did not match the documented/expected structure."""
