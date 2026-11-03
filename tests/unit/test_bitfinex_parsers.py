from decimal import Decimal

import pytest

from fundpilot.core.models import BookSide
from fundpilot.exchanges.bitfinex import parsers
from fundpilot.exchanges.errors import ResponseParseError
from tests.conftest import FIXED_NOW, load_fixture


def test_ticker_fields_and_decimal_precision() -> None:
    t = parsers.parse_funding_ticker(
        load_fixture("funding_ticker.json"), symbol="fUST", observed_at=FIXED_NOW
    )
    assert t.frr == Decimal("0.000123456789012345")  # no float rounding
    assert t.bid == Decimal("0.0002")
    assert t.bid_period_days == 30
    assert t.ask_period_days == 2
    assert t.daily_change == Decimal("-0.00001")
    assert t.frr_amount_available == Decimal("1234567.89")
    assert t.observed_at == FIXED_NOW


def test_ticker_too_short_raises() -> None:
    with pytest.raises(ResponseParseError):
        parsers.parse_funding_ticker([1, 2, 3], symbol="fUST", observed_at=FIXED_NOW)


def test_aggregated_book_sides_from_amount_sign() -> None:
    book = parsers.parse_funding_book(
        load_fixture("funding_book_p0.json"), symbol="fUST", precision="P0", captured_at=FIXED_NOW
    )
    asks = book.side(BookSide.ASK)
    bids = book.side(BookSide.BID)
    assert len(asks) == 2 and len(bids) == 2
    assert all(level.amount >= 0 for level in book.levels)
    assert bids[0].amount == Decimal(50000)
    assert bids[0].period_days == 30
    assert asks[0].count == 5


def test_raw_book_field_order_differs() -> None:
    book = parsers.parse_funding_raw_book(
        load_fixture("funding_book_r0.json"), symbol="fUST", captured_at=FIXED_NOW
    )
    first = book.levels[0]
    assert first.offer_id == 4001
    assert first.period_days == 2
    assert first.rate == Decimal("0.0002")
    assert book.precision == "R0"
    assert book.levels[1].side is BookSide.BID


def test_funding_stats_scaling_keeps_raw() -> None:
    stats = parsers.parse_funding_stats(
        load_fixture("funding_stats.json"), symbol="fUST", frr_scale=Decimal(365)
    )
    first = stats[0]
    assert first.frr_raw == Decimal("5.479452e-7")
    assert first.frr == Decimal("5.479452e-7") * 365
    assert first.avg_period_days == Decimal("11.3")
    assert first.funding_amount == Decimal("512000000.12345678")
    assert first.funding_amount_used == Decimal("410000000.5")
    assert first.funding_below_threshold == Decimal("1200.75")
    assert first.observed_at.year == 2025


def test_trades_keep_signed_amount() -> None:
    trades = parsers.parse_funding_trades(load_fixture("funding_trades.json"), symbol="fUST")
    assert trades[0].amount == Decimal("-500.5")
    assert trades[0].executed_at.microsecond == 123_000
    assert trades[1].period_days == 30


def test_candles_open_close_order() -> None:
    candles = parsers.parse_candles(
        load_fixture("funding_candles.json"),
        symbol="fUST",
        candle_key="trade:1h:fUST:a30:p2:p30",
        timeframe="1h",
    )
    c = candles[0]
    assert (c.open, c.close, c.high, c.low) == (
        Decimal("0.0002"),
        Decimal("0.00021"),
        Decimal("0.00025"),
        Decimal("0.00019"),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        [["a", 2, 1, 1]],
        [[Decimal("0.1"), Decimal("2.5"), 1, 1]],  # fractional period
        [[None, 2, 1, 1]],
        [[True, 2, 1, 1]],
    ],
)
def test_malformed_book_rows(payload: object) -> None:
    with pytest.raises(ResponseParseError):
        parsers.parse_funding_book(payload, symbol="fUST", precision="P0", captured_at=FIXED_NOW)


def test_empty_payloads_are_empty_lists() -> None:
    assert parsers.parse_funding_trades([], symbol="fUST") == []
    assert parsers.parse_funding_stats(None, symbol="fUST", frr_scale=Decimal(1)) == []


def test_platform_status() -> None:
    assert parsers.parse_platform_status([1]) is True
    assert parsers.parse_platform_status([0]) is False
