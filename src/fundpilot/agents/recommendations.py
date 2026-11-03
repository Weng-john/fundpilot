"""Deterministic validation of configuration recommendations.

Only whitelisted parameters with bounded values can be proposed. Every proposal is stored as
``proposed`` and must be accepted by a human; accepting only records the decision - the
operator edits the configuration file. Risk-increasing proposals are flagged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.core.time import utc_now
from fundpilot.data.db import session_scope
from fundpilot.data.models import AgentRecommendationRow


@dataclass(frozen=True, slots=True)
class ParameterRule:
    low: Decimal
    high: Decimal
    integer: bool = False
    # +1: raising the value increases risk; -1: lowering it increases risk
    risk_direction: int = 1
    description: str = ""


RULES: dict[str, ParameterRule] = {
    "risk.max_allocation_fraction": ParameterRule(
        Decimal(0), Decimal(1), False, 1, "share of capital that may be deployed"
    ),
    "risk.min_liquidity_fraction": ParameterRule(
        Decimal(0), Decimal(1), False, -1, "share of capital kept idle"
    ),
    "risk.max_single_offer": ParameterRule(
        Decimal(150), Decimal(10_000_000), False, 1, "largest single offer"
    ),
    "risk.min_daily_rate": ParameterRule(
        Decimal(0), Decimal("0.01"), False, -1, "lowest acceptable daily rate"
    ),
    "risk.max_period_days": ParameterRule(
        Decimal(2), Decimal(120), True, 1, "longest funding period"
    ),
    "risk.max_period_concentration": ParameterRule(
        Decimal(0), Decimal(1), False, 1, "max share in one period"
    ),
    "strategy.tilt": ParameterRule(
        Decimal(0), Decimal(2), False, 1, "ladder tilt towards long durations"
    ),
    "strategy.reprice_after_minutes": ParameterRule(
        Decimal(5), Decimal(1440), True, 0, "delay before repricing unfilled offers"
    ),
}


@dataclass(frozen=True, slots=True)
class Validation:
    ok: bool
    errors: tuple[str, ...]
    value: Decimal | None
    risk_increasing: bool


def validate(parameter: str, value: str, current: str | None = None) -> Validation:
    rule = RULES.get(parameter)
    if rule is None:
        return Validation(
            False, (f"parameter {parameter!r} is not on the allow-list",), None, False
        )
    try:
        v = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Validation(False, (f"value {value!r} is not a number",), None, False)
    errors = []
    if not v.is_finite() or not rule.low <= v <= rule.high:
        errors.append(f"value {value} outside [{rule.low}, {rule.high}]")
    if rule.integer and v != v.to_integral_value():
        errors.append("value must be an integer")
    risk_up = True  # unknown current value: treat as risk-increasing (conservative)
    if current is not None:
        try:
            delta = v - Decimal(str(current))
            risk_up = rule.risk_direction != 0 and delta * rule.risk_direction > 0
        except (InvalidOperation, ValueError):
            pass
    elif rule.risk_direction == 0:
        risk_up = False
    return Validation(not errors, tuple(errors), v, risk_up)


class RecommendationStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def propose(
        self,
        parameter: str,
        value: str,
        rationale: str,
        source: str,
        current: str | None = None,
        now: datetime | None = None,
    ) -> tuple[int | None, Validation]:
        check = validate(parameter, value, current)
        if not check.ok:
            return None, check
        with session_scope(self._sessions) as s:
            row = AgentRecommendationRow(
                created_at=now or utc_now(),
                source=source[:64],
                parameter=parameter,
                proposed_value=str(check.value),
                rationale=rationale[:4000],
                risk_increasing=check.risk_increasing,
                status="proposed",
            )
            s.add(row)
            s.flush()
            return row.id, check

    def decide(self, rec_id: int, accept: bool, by: str, now: datetime | None = None) -> str:
        with session_scope(self._sessions) as s:
            row = s.get(AgentRecommendationRow, rec_id)
            if row is None:
                raise KeyError(f"recommendation {rec_id} not found")
            if row.status != "proposed":
                raise ValueError(f"recommendation {rec_id} is already {row.status}")
            row.status = "accepted" if accept else "rejected"
            row.decided_by, row.decided_at = by, now or utc_now()
            return row.status

    def list(self, status: str | None = "proposed") -> list[AgentRecommendationRow]:
        with session_scope(self._sessions) as s:
            stmt = select(AgentRecommendationRow).order_by(AgentRecommendationRow.id)
            if status:
                stmt = stmt.where(AgentRecommendationRow.status == status)
            return list(s.scalars(stmt))
