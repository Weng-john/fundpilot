"""Decimal quantisation helpers for monetary amounts."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal

from fundpilot.core.rates import to_decimal


def quantize_down(value: Decimal, places: int = 8) -> Decimal:
    """Round towards zero. Use for amounts sent to an exchange so we never exceed balance."""
    return to_decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)


def quantize_half_even(value: Decimal, places: int = 8) -> Decimal:
    """Banker's rounding for reporting and aggregation."""
    return to_decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
