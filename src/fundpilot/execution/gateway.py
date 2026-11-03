"""Execution gateway.

Single choke point for acting on offers. Flow for every decision:

    decision -> RiskManager.evaluate -> approvals queue / approved items
             -> idempotency check -> backend (dry_run: nothing, paper: virtual account,
                live: refused - not implemented) -> audit log

Live execution is deliberately unavailable: :class:`LiveExecutionDisabledError` is raised.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from fundpilot.config import ExecutionMode
from fundpilot.core.portfolio import PortfolioState
from fundpilot.data.db import session_scope
from fundpilot.data.models import ExecutionAuditRow, IdempotencyKeyRow
from fundpilot.data.repository import insert_ignore
from fundpilot.execution.controls import Controls, request_to_payload
from fundpilot.risk.manager import HealthStatus, RiskAlert, RiskManager, RiskVerdict
from fundpilot.strategies.base import Decision, MarketState, OfferRequest

logger = logging.getLogger(__name__)


class LiveExecutionDisabledError(RuntimeError):
    """Live execution is not implemented and requires explicit human approval to build."""


class TransientExecutionError(RuntimeError):
    """A backend failure that may succeed on retry (timeouts, 5xx)."""


class ExecutionBackend(Protocol):
    def place(self, request: OfferRequest) -> str: ...

    def cancel(self, offer_id: str) -> None: ...


@dataclass(slots=True)
class ExecutionReport:
    mode: ExecutionMode
    verdict: RiskVerdict
    placed: list[str] = field(default_factory=list)  # exchange refs
    cancelled: list[str] = field(default_factory=list)
    duplicates: int = 0
    failures: list[str] = field(default_factory=list)
    approvals_requested: list[int] = field(default_factory=list)
    approved_executed: list[int] = field(default_factory=list)
    alerts: tuple[RiskAlert, ...] = ()


def idempotency_key(
    account: str, decision_id: str, action: str, index: int, payload: dict[str, Any]
) -> str:
    raw = json.dumps([account, decision_id, action, index, payload], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class ExecutionGateway:
    def __init__(
        self,
        *,
        mode: ExecutionMode,
        account: str,
        risk: RiskManager,
        controls: Controls,
        sessions: sessionmaker[Session],
        backend: ExecutionBackend | None = None,
        approval_ttl: timedelta = timedelta(hours=1),
        max_retries: int = 2,
        retry_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if mode is ExecutionMode.LIVE:
            raise LiveExecutionDisabledError(
                "live execution is not implemented; it requires an explicit, human-approved "
                "Phase 7 change"
            )
        if mode is ExecutionMode.PAPER and backend is None:
            raise ValueError("paper mode needs a backend (virtual account)")
        self.mode = mode
        self.account = account
        self.risk = risk
        self.controls = controls
        self._sessions = sessions
        self._backend = backend
        self._approval_ttl = approval_ttl
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._sleep = sleep

    def execute(
        self,
        decision: Decision,
        portfolio: PortfolioState,
        market: MarketState,
        health: HealthStatus,
        decision_id: str,
    ) -> ExecutionReport:
        now = market.as_of
        stop, reason = self.controls.emergency_state()
        if stop and not health.emergency_stop:
            health = replace(health, emergency_stop=True, emergency_reason=reason)
        # previously approved requests re-enter through the risk manager like any offer
        approved = (
            []
            if health.emergency_stop
            else self.controls.take_approved(self.account, self.mode.value, now)
        )
        merged = Decision(
            decision.place + tuple(r for _, r in approved), decision.cancel, decision.notes
        )
        verdict = self.risk.evaluate(merged, portfolio, market, health)
        report = ExecutionReport(self.mode, verdict, alerts=verdict.alerts)
        report.approved_executed = [i for i, _ in approved]
        for alert in verdict.alerts:
            self.controls.alert("risk", alert, now)
        for req in verdict.needs_approval:
            report.approvals_requested.append(
                self.controls.request_approval(
                    self.account,
                    self.mode.value,
                    req,
                    "amount above approval threshold",
                    self._approval_ttl,
                    now,
                )
            )

        for i, offer_id in enumerate(verdict.decision.cancel):
            payload = {"offer_id": offer_id}
            self._act(
                "cancel",
                i,
                payload,
                decision_id,
                now,
                report,
                partial(self._cancel, offer_id),
            )
        for i, req in enumerate(verdict.decision.place):
            payload = request_to_payload(req)
            self._act("place", i, payload, decision_id, now, report, partial(self._place, req))
        return report

    # -- internals -------------------------------------------------------------
    def _place(self, req: OfferRequest) -> str | None:
        if self.mode is ExecutionMode.DRY_RUN or self._backend is None:
            return None
        return self._backend.place(req)

    def _cancel(self, offer_id: str) -> str | None:
        if self.mode is ExecutionMode.DRY_RUN or self._backend is None:
            return None
        self._backend.cancel(offer_id)
        return offer_id

    def _act(
        self,
        action: str,
        index: int,
        payload: dict[str, Any],
        decision_id: str,
        now: datetime,
        report: ExecutionReport,
        call: Callable[[], str | None],
    ) -> None:
        key = idempotency_key(self.account, decision_id, action, index, payload)
        with session_scope(self._sessions) as s:
            fresh = insert_ignore(
                s,
                IdempotencyKeyRow,
                [
                    {
                        "key": key,
                        "account": self.account,
                        "mode": self.mode.value,
                        "created_at": now,
                        "exchange_ref": None,
                    }
                ],
            )
        if not fresh:
            report.duplicates += 1
            self._audit(now, action, key, "duplicate", None, payload, "already processed")
            return
        ref: str | None = None
        error: str | None = None
        for attempt in range(self._max_retries + 1):
            try:
                ref = call()
                error = None
                break
            except TransientExecutionError as exc:
                error = str(exc)
                if attempt < self._max_retries:
                    self._sleep(self._retry_delay * (2**attempt))
            except Exception as exc:  # non-retryable: record and continue with the rest
                error = f"{type(exc).__name__}: {exc}"
                break
        if error is not None:
            report.failures.append(f"{action}: {error}")
            self._audit(now, action, key, "failed", None, payload, error)
            return
        outcome = "simulated" if self.mode is ExecutionMode.DRY_RUN else "submitted"
        with session_scope(self._sessions) as s:
            row = s.get(IdempotencyKeyRow, key)
            if row is not None:
                row.exchange_ref = ref
        (report.placed if action == "place" else report.cancelled).append(ref or "dry-run")
        self._audit(now, action, key, outcome, ref, payload, None)

    def _audit(
        self,
        now: datetime,
        action: str,
        key: str,
        outcome: str,
        ref: str | None,
        payload: dict[str, Any],
        detail: str | None,
    ) -> None:
        with session_scope(self._sessions) as s:
            s.add(
                ExecutionAuditRow(
                    at=now,
                    account=self.account,
                    mode=self.mode.value,
                    action=action,
                    idempotency_key=key,
                    outcome=outcome,
                    exchange_ref=ref,
                    payload=payload,
                    detail=detail,
                )
            )
