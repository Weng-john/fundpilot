from datetime import UTC, datetime, timedelta, timezone

import pytest

from fundpilot.core.time import ensure_utc, from_ms, to_ms


def test_ms_roundtrip_preserves_milliseconds() -> None:
    mts = 1761955260123
    dt = from_ms(mts)
    assert dt.tzinfo is UTC
    assert dt.microsecond == 123_000
    assert to_ms(dt) == mts


def test_negative_epoch_ms() -> None:
    assert to_ms(from_ms(-1)) == -1


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValueError):
        to_ms(datetime(2026, 1, 1))  # noqa: DTZ001


def test_non_utc_converted() -> None:
    tz = timezone(timedelta(hours=8))
    dt = datetime(2026, 1, 1, 8, tzinfo=tz)
    assert ensure_utc(dt) == datetime(2026, 1, 1, tzinfo=UTC)


def test_from_ms_rejects_non_int() -> None:
    with pytest.raises(TypeError):
        from_ms(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        from_ms(True)
