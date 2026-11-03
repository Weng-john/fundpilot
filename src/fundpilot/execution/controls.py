"""Persistent operator controls: emergency stop, manual approvals, alerts."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.core.portfolio import RateType
from fundpilot.core.time import utc_now
from fundpilot.data.db import session_scope
from fundpilot.data.models import AlertRow, ApprovalRequestRow, ControlFlagRow
from fundpilot.risk.manager import CREDITS_NOT_RECALLABLE, AlertSeverity, RiskAlert
from fundpilot.strategies.base import OfferRequest

logger = logging.getLogger(__name__)
EMERGENCY_STOP = "emergency_stop"


def request_to_payload(req: OfferRequest) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Decimal | RateType) else v) for k, v in asdict(req).items()}


def request_from_payload(payload: dict[str, Any]) -> OfferRequest:
    return OfferRequest(
        rate_type=RateType(payload["rate_type"]),
        rate=None if payload["rate"] is None else Decimal(payload["rate"]),
        period_days=int(payload["period_days"]),
        amount=Decimal(payload["amount"]),
        tag=str(payload.get("tag", "")),
    )


class Controls:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    # -- emergency stop ------------------------------------------------------
    def emergency_stop(self, reason: str, by: str, now: datetime | None = None) -> str:
        self._set_flag(EMERGENCY_STOP, True, reason, by, now)
        message = f"Emergency stop activated: new offers suspended. {CREDITS_NOT_RECALLABLE}"
        self.alert("controls", RiskAlert("emergency_stop", AlertSeverity.CRITICAL, message), now)
        return message

    def resume(self, by: str, now: datetime | None = None) -> None:
        self._set_flag(EMERGENCY_STOP, False, "resumed", by, now)

    def emergency_state(self) -> tuple[bool, str | None]:
        with session_scope(self._sessions) as s:
            row = s.get(ControlFlagRow, EMERGENCY_STOP)
            return (row.active, row.reason) if row else (False, None)

    def _set_flag(
        self, name: str, active: bool, reason: str, by: str, now: datetime | None
    ) -> None:
        with session_scope(self._sessions) as s:
            row = s.get(ControlFlagRow, name)
            if row is None:
                row = ControlFlagRow(
                    name=name,
                    active=active,
                    reason=reason,
                    updated_by=by,
                    updated_at=now or utc_now(),
                )
                s.add(row)
            else:
                row.active, row.reason, row.updated_by = active, reason, by
                row.updated_at = now or utc_now()

    # -- approvals -----------------------------------------------------------
    def request_approval(
        self,
        account: str,
        mode: str,
        req: OfferRequest,
        reason: str,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> int:
        now = now or utc_now()
        with session_scope(self._sessions) as s:
            row = ApprovalRequestRow(
                account=account,
                mode=mode,
                created_at=now,
                expires_at=now + ttl,
                status="pending",
                payload=request_to_payload(req),
                reason=reason,
            )
            s.add(row)
            s.flush()
            return row.id

    def decide_approval(
        self, approval_id: int, approve: bool, by: str, now: datetime | None = None
    ) -> str:
        now = now or utc_now()
        with session_scope(self._sessions) as s:
            row = s.get(ApprovalRequestRow, approval_id)
            if row is None:
                raise KeyError(f"approval {approval_id} not found")
            if row.status != "pending":
                raise ValueError(f"approval {approval_id} is {row.status}, not pending")
            if row.expires_at <= now:
                row.status = "expired"
                return row.status
            row.status = "approved" if approve else "rejected"
            row.decided_by, row.decided_at = by, now
            return row.status

    def take_approved(
        self, account: str, mode: str, now: datetime | None = None
    ) -> list[tuple[int, OfferRequest]]:
        """Approved, unexpired requests; marked executed so they are only taken once."""
        now = now or utc_now()
        out = []
        with session_scope(self._sessions) as s:
            rows = s.scalars(
                select(ApprovalRequestRow).where(
                    ApprovalRequestRow.account == account,
                    ApprovalRequestRow.mode == mode,
                    ApprovalRequestRow.status.in_(["approved", "pending"]),
                )
            ).all()
            for row in rows:
                if row.expires_at <= now:
                    row.status = "expired"
                elif row.status == "approved":
                    row.status = "executed"
                    out.append((row.id, request_from_payload(row.payload)))
        return out

    def pending_approvals(self, account: str | None = None) -> list[ApprovalRequestRow]:
        with session_scope(self._sessions) as s:
            stmt = select(ApprovalRequestRow).where(ApprovalRequestRow.status == "pending")
            if account:
                stmt = stmt.where(ApprovalRequestRow.account == account)
            return list(s.scalars(stmt.order_by(ApprovalRequestRow.id)))

    # -- alerts --------------------------------------------------------------
    def alert(
        self,
        source: str,
        alert: RiskAlert,
        now: datetime | None = None,
        dedupe_window: timedelta = timedelta(minutes=30),
    ) -> bool:
        """Store an alert unless the same code was raised within ``dedupe_window``."""
        now = now or utc_now()
        with session_scope(self._sessions) as s:
            recent = s.scalar(
                select(AlertRow.id)
                .where(AlertRow.code == alert.code, AlertRow.at > now - dedupe_window)
                .limit(1)
            )
            if recent is not None:
                return False
            s.add(
                AlertRow(
                    at=now,
                    source=source,
                    code=alert.code,
                    severity=str(alert.severity),
                    message=alert.message,
                )
            )
        log = logger.critical if str(alert.severity) == "critical" else logger.warning
        log("[%s] %s: %s", source, alert.code, alert.message)
        return True

    def recent_alerts(self, limit: int = 50) -> list[AlertRow]:
        with session_scope(self._sessions) as s:
            return list(s.scalars(select(AlertRow).order_by(AlertRow.at.desc()).limit(limit)))
