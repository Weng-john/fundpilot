"""UTC time helpers.

All timestamps inside FundPilot are timezone-aware UTC ``datetime`` objects.
Naive datetimes are rejected rather than silently assumed to be UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime

MS_PER_SECOND = 1000


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(tz=UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` converted to UTC; raise if it is naive."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"naive datetime is not allowed: {value!r}")
    return value.astimezone(UTC)


def from_ms(mts: int) -> datetime:
    """Convert a Unix epoch timestamp in milliseconds to an aware UTC datetime."""
    if isinstance(mts, bool) or not isinstance(mts, int):
        raise TypeError(f"millisecond timestamp must be int, got {type(mts).__name__}")
    seconds, millis = divmod(mts, MS_PER_SECOND)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=millis * 1000)


def to_ms(value: datetime) -> int:
    """Convert an aware datetime to Unix epoch milliseconds (truncating sub-ms)."""
    value = ensure_utc(value)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return (delta.days * 86_400 + delta.seconds) * MS_PER_SECOND + delta.microseconds // 1000
