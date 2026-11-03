from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.config import ExecutionMode
from fundpilot.core.portfolio import PortfolioState, RateType
from fundpilot.data.db import session_scope
from fundpilot.data.models import AlertRow, ExecutionAuditRow
from fundpilot.execution.controls import Controls
from fundpilot.execution.gateway import (
    ExecutionGateway,
    LiveExecutionDisabledError,
    TransientExecutionError,
)
from fundpilot.risk.manager import HealthStatus, RiskLimits, RiskManager
from fundpilot.strategies.base import Decision, OfferRequest
from tests.conftest import FIXED_NOW
from tests.helpers import state

D = Decimal
OK = HealthStatus(FIXED_NOW, platform_operational=True, last_market_data_at=FIXED_NOW)
REQ = OfferRequest(RateType.FIXED, D("0.0002"), 2, D(1000))


class FakeBackend:
    def __init__(self, fail_times: int = 0, error: type[Exception] = TransientExecutionError):
        self.placed: list[OfferRequest] = []
        self.cancelled: list[str] = []
        self.fail_times = fail_times
        self.error = error

    def place(self, request: OfferRequest) -> str:
        if self.fail_times:
            self.fail_times -= 1
            raise self.error("boom")
        self.placed.append(request)
        return f"x{len(self.placed)}"

    def cancel(self, offer_id: str) -> None:
        self.cancelled.append(offer_id)


def _gw(
    sessions: sessionmaker[Session],
    mode: ExecutionMode = ExecutionMode.PAPER,
    backend: FakeBackend | None = None,
    limits: RiskLimits | None = None,
) -> ExecutionGateway:
    return ExecutionGateway(
        mode=mode,
        account="acct",
        risk=RiskManager(limits),
        controls=Controls(sessions),
        sessions=sessions,
        backend=backend or FakeBackend(),
        sleep=lambda _: None,
    )


def _pf() -> PortfolioState:
    return PortfolioState(FIXED_NOW, "UST", D(10000))


def _audit(sessions: sessionmaker[Session]) -> list[tuple[str, str]]:
    with session_scope(sessions) as s:
        return [(r.action, r.outcome) for r in s.scalars(select(ExecutionAuditRow))]


def test_live_mode_is_refused(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(LiveExecutionDisabledError):
        _gw(sessions, ExecutionMode.LIVE)


def test_dry_run_never_calls_backend(sessions: sessionmaker[Session]) -> None:
    backend = FakeBackend()
    report = _gw(sessions, ExecutionMode.DRY_RUN, backend).execute(
        Decision((REQ,)), _pf(), state(FIXED_NOW), OK, "d1"
    )
    assert backend.placed == [] and report.placed == ["dry-run"]
    assert _audit(sessions) == [("place", "simulated")]


def test_paper_places_and_duplicate_is_blocked(sessions: sessionmaker[Session]) -> None:
    backend = FakeBackend()
    gw = _gw(sessions, backend=backend)
    first = gw.execute(Decision((REQ,)), _pf(), state(FIXED_NOW), OK, "d1")
    again = gw.execute(Decision((REQ,)), _pf(), state(FIXED_NOW), OK, "d1")
    assert first.placed == ["x1"] and again.duplicates == 1
    assert len(backend.placed) == 1
    assert _audit(sessions) == [("place", "submitted"), ("place", "duplicate")]


def test_transient_errors_are_retried(sessions: sessionmaker[Session]) -> None:
    backend = FakeBackend(fail_times=2)
    report = _gw(sessions, backend=backend).execute(
        Decision((REQ,)), _pf(), state(FIXED_NOW), OK, "d1"
    )
    assert report.placed == ["x1"] and report.failures == []


def test_permanent_errors_are_recorded(sessions: sessionmaker[Session]) -> None:
    backend = FakeBackend(fail_times=1, error=ValueError)
    report = _gw(sessions, backend=backend).execute(
        Decision((REQ,)), _pf(), state(FIXED_NOW), OK, "d1"
    )
    assert report.placed == [] and "ValueError" in report.failures[0]
    assert _audit(sessions) == [("place", "failed")]


def test_emergency_stop_blocks_and_persists(sessions: sessionmaker[Session]) -> None:
    controls = Controls(sessions)
    message = controls.emergency_stop("drill", by="test", now=FIXED_NOW)
    assert "cannot be recalled" in message
    backend = FakeBackend()
    report = _gw(sessions, backend=backend).execute(
        Decision((REQ,)), _pf(), state(FIXED_NOW), OK, "d1"
    )
    assert report.verdict.new_offers_blocked and backend.placed == []
    controls.resume(by="test", now=FIXED_NOW)
    assert controls.emergency_state() == (False, "resumed")
    with session_scope(sessions) as s:
        assert "emergency_stop" in s.scalars(select(AlertRow.code)).all()


def test_manual_approval_flow(sessions: sessionmaker[Session]) -> None:
    backend = FakeBackend()
    gw = _gw(sessions, backend=backend, limits=RiskLimits(approval_threshold=D(500)))
    report = gw.execute(Decision((REQ,)), _pf(), state(FIXED_NOW), OK, "d1")
    assert backend.placed == [] and len(report.approvals_requested) == 1
    approval_id = report.approvals_requested[0]
    controls = Controls(sessions)
    assert controls.decide_approval(approval_id, True, by="human", now=FIXED_NOW) == "approved"
    # approved items still go through the risk manager: raise the threshold to let it pass
    gw.risk = RiskManager(RiskLimits())
    later = state(FIXED_NOW + timedelta(minutes=1))
    report2 = gw.execute(Decision(), _pf(), later, OK, "d2")
    assert report2.approved_executed == [approval_id] and len(backend.placed) == 1
    report3 = gw.execute(Decision(), _pf(), later, OK, "d3")
    assert report3.approved_executed == []  # executed only once
    with pytest.raises(ValueError):
        controls.decide_approval(approval_id, True, by="human")


def test_expired_approval(sessions: sessionmaker[Session]) -> None:
    controls = Controls(sessions)
    aid = controls.request_approval(
        "acct", "paper", REQ, "big", timedelta(minutes=5), now=FIXED_NOW
    )
    assert controls.decide_approval(aid, True, "h", now=FIXED_NOW + timedelta(hours=1)) == "expired"


def test_alert_dedupe(sessions: sessionmaker[Session]) -> None:
    from fundpilot.risk.manager import AlertSeverity, RiskAlert  # noqa: PLC0415

    controls = Controls(sessions)
    a = RiskAlert("x", AlertSeverity.WARNING, "m")
    assert controls.alert("t", a, FIXED_NOW)
    assert not controls.alert("t", a, FIXED_NOW + timedelta(minutes=5))
    assert controls.alert("t", a, FIXED_NOW + timedelta(hours=1))
