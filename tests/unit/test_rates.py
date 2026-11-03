from decimal import Decimal

import pytest

from fundpilot.core.money import quantize_down, quantize_half_even
from fundpilot.core.rates import (
    DailyRate,
    accrued_interest,
    net_interest,
    platform_fee,
    to_decimal,
)


def test_daily_fraction_to_apr_is_simple_times_365() -> None:
    assert DailyRate.from_daily_fraction("0.0002").apr() == Decimal("0.0730")


def test_daily_percent_roundtrip() -> None:
    rate = DailyRate.from_daily_percent("0.02")
    assert rate.value == Decimal("0.0002")
    assert rate.daily_percent() == Decimal("0.0200")


def test_from_apr_inverts_apr() -> None:
    rate = DailyRate.from_apr("0.073")
    assert rate.value == Decimal("0.0002")
    assert rate.apr() == Decimal("0.0730")


def test_apy_exceeds_apr_for_positive_rates() -> None:
    rate = DailyRate.from_daily_fraction("0.0002")
    apy = rate.apy()
    assert apy > rate.apr()
    # (1 + 0.0002)^365 - 1 ~= 0.07571
    assert Decimal("0.0757") < apy < Decimal("0.0758")


def test_apy_rejects_non_positive_periods() -> None:
    with pytest.raises(ValueError):
        DailyRate.from_daily_fraction("0.0002").apy(0)


def test_floats_are_rejected() -> None:
    with pytest.raises(TypeError):
        to_decimal(0.1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        DailyRate(0.0002)  # type: ignore[arg-type]


def test_non_finite_rejected() -> None:
    with pytest.raises(ValueError):
        to_decimal("NaN")


def test_net_of_fee() -> None:
    assert DailyRate.from_daily_fraction("0.0002").net_of_fee(Decimal("0.15")).value == Decimal(
        "0.000170"
    )
    with pytest.raises(ValueError):
        DailyRate.from_daily_fraction("0.0002").net_of_fee(Decimal("1.5"))


def test_accrued_interest_one_day() -> None:
    interest = accrued_interest(Decimal(10_000), DailyRate.from_daily_fraction("0.0002"), 86_400)
    assert interest == Decimal(2)


def test_accrued_interest_is_pro_rated_per_second() -> None:
    rate = DailyRate.from_daily_fraction("0.0002")
    one_hour = accrued_interest(Decimal(10_000), rate, 3600)
    assert one_hour * 24 == Decimal(2)


def test_accrued_interest_precision_is_exact() -> None:
    # 1 second of interest on a small principal must not lose precision.
    interest = accrued_interest(Decimal("150"), DailyRate.from_daily_fraction("0.0001"), 1)
    assert interest * Decimal(86_400) == Decimal("0.015")


def test_accrued_interest_rejects_negative() -> None:
    rate = DailyRate.from_daily_fraction("0.0002")
    with pytest.raises(ValueError):
        accrued_interest(Decimal(-1), rate, 10)
    with pytest.raises(ValueError):
        accrued_interest(Decimal(1), rate, -10)


def test_fee_and_net_interest() -> None:
    gross = Decimal("2.00")
    assert platform_fee(gross, Decimal("0.15")) == Decimal("0.3000")
    assert net_interest(gross, Decimal("0.15")) == Decimal("1.7000")
    assert net_interest(gross, Decimal("0.18")) == Decimal("1.6400")
    with pytest.raises(ValueError):
        platform_fee(gross, Decimal("-0.1"))


def test_quantize() -> None:
    assert quantize_down(Decimal("1.123456789")) == Decimal("1.12345678")
    assert quantize_down(Decimal("-1.123456789")) == Decimal("-1.12345678")
    assert quantize_half_even(Decimal("0.125"), 2) == Decimal("0.12")
    assert quantize_half_even(Decimal("0.135"), 2) == Decimal("0.14")
