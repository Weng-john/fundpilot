"""Normalised Aave reserve state."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from fundpilot.core.models import DataOrigin
from fundpilot.core.time import ensure_utc
from fundpilot.defi.aave.math import RateModel, rates


class ReserveSnapshot(BaseModel):
    """State of one Aave reserve; amounts in token units, rates as annual fractions (APR)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol: str = "aave-v3"
    chain: str
    symbol: str
    underlying: str
    observed_at: datetime
    total_supplied: Decimal  # aToken total supply
    total_debt: Decimal  # variable debt token total supply
    available_liquidity: Decimal  # virtual underlying balance (withdrawable now)
    supply_apr: Decimal
    borrow_apr: Decimal
    reserve_factor: Decimal
    supply_cap: Decimal | None  # None = no cap
    optimal_usage: Decimal | None = None
    base_rate: Decimal | None = None
    slope1: Decimal | None = None
    slope2: Decimal | None = None
    is_active: bool = True
    is_frozen: bool = False
    is_paused: bool = False
    origin: DataOrigin = DataOrigin.EXCHANGE_API

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.symbol}"

    @property
    def utilization(self) -> Decimal:
        total = self.available_liquidity + self.total_debt
        return self.total_debt / total if total > 0 else Decimal(0)

    @property
    def model(self) -> RateModel | None:
        if None in (self.optimal_usage, self.base_rate, self.slope1, self.slope2):
            return None
        return RateModel(self.optimal_usage, self.base_rate, self.slope1, self.slope2)  # type: ignore[arg-type]

    def cap_headroom(self) -> Decimal | None:
        if self.supply_cap is None:
            return None
        return max(Decimal(0), self.supply_cap - self.total_supplied)

    def supply_apr_with(self, delta: Decimal) -> Decimal:
        """Supply APR after our flow ``delta`` (model-based); current APR if no model."""
        model = self.model
        if model is None:
            return self.supply_apr
        available = max(Decimal(0), self.available_liquidity + delta)
        return rates(model, self.total_debt, available, self.reserve_factor)[0]

    @property
    def tradable(self) -> bool:
        return self.is_active and not self.is_frozen and not self.is_paused
