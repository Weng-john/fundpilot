from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.api.app import create_app
from fundpilot.backtesting.synthetic import SyntheticConfig, generate
from fundpilot.config import Settings
from fundpilot.core.time import utc_now
from fundpilot.data import repository
from fundpilot.data.db import session_scope
from fundpilot.execution.controls import Controls
from tests.unit.test_paper import Clock, _engine


def _client(
    sessions: sessionmaker[Session], tmp_path: Path, token: str | None = None
) -> TestClient:
    settings = Settings(
        _env_file=None,
        api_reports_root=str(tmp_path / "outputs"),
        api_models_root=str(tmp_path / "models"),
        api_token=SecretStr(token) if token else None,
    )
    return TestClient(create_app(settings, sessions))


@pytest.fixture
def seeded(sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    start = utc_now() - timedelta(days=1)
    t, b, tr = generate(SyntheticConfig(start=start, days=1, step=timedelta(minutes=30)))
    with session_scope(sessions) as s:
        repository.save_tickers(s, t)
        repository.save_trades(s, tr)
        repository.save_book_snapshot(s, b[-1])
    return sessions


def test_health_and_defaults(sessions: sessionmaker[Session], tmp_path: Path) -> None:
    body = _client(sessions, tmp_path).get("/api/health").json()
    assert body["execution_mode"] == "dry_run" and body["write_api_enabled"] is False


def test_market_endpoints_exclude_synthetic_by_default(
    seeded: sessionmaker[Session], tmp_path: Path
) -> None:
    c = _client(seeded, tmp_path)
    assert c.get("/api/market/frr").json()["points"] == []
    with_syn = c.get("/api/market/frr", params={"include_synthetic": True}).json()
    assert len(with_syn["points"]) > 10 and with_syn["unit"] == "daily fraction"
    book = c.get("/api/market/book", params={"include_synthetic": True}).json()
    assert book["origin"] == "synthetic" and book["levels"]
    dist = c.get("/api/market/rate-distribution", params={"include_synthetic": True}).json()
    assert sum(b["count"] for b in dist["bins"]) > 0
    ov = c.get("/api/overview", params={"include_synthetic": True}).json()
    assert ov["market"]["frr_daily"] is not None
    assert c.get("/api/market/stats").json()["points"] == []


def test_paper_account_views(sessions: sessionmaker[Session], tmp_path: Path) -> None:
    clock = Clock()
    engine, _ = _engine(sessions, clock)
    for _ in range(3):
        engine.step()
        clock.tick()
    c = _client(sessions, tmp_path)
    acct = c.get("/api/overview", params={"account": "paper-test"}).json()["account"]
    assert acct["label"].startswith("SIMULATED")
    assert acct["active_credits"] > 0 and acct["maturity_distribution"]
    risk = c.get("/api/risk", params={"account": "paper-test"}).json()
    assert "cannot be recalled" in risk["credits_note"]
    execution = c.get("/api/execution", params={"account": "paper-test"}).json()
    assert execution["counts"].get("submitted", 0) > 0
    eq = c.get("/api/accounts/paper-test/equity", params={"days": 365}).json()
    assert eq["label"] == "SIMULATED"


def test_emergency_stop_requires_token(sessions: sessionmaker[Session], tmp_path: Path) -> None:
    body = {"reason": "dashboard drill"}
    assert (
        _client(sessions, tmp_path).post("/api/controls/emergency-stop", json=body).status_code
        == 403
    )
    c = _client(sessions, tmp_path, token="s3cret")
    assert (
        c.post(
            "/api/controls/emergency-stop", json=body, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    ok = c.post(
        "/api/controls/emergency-stop", json=body, headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200 and "cannot be recalled" in ok.json()["message"]
    assert Controls(sessions).emergency_state()[0] is True
    # there is no resume endpoint: resuming is CLI-only
    assert c.post("/api/controls/resume").status_code in {404, 405}


def test_experiments_and_path_traversal(sessions: sessionmaker[Session], tmp_path: Path) -> None:
    out = tmp_path / "outputs" / "exp" / "1"
    out.mkdir(parents=True)
    (out / "report.json").write_text(
        json.dumps({"kind": "simulated", "data_label": "SYNTHETIC", "runs": []})
    )
    (tmp_path / "secret.json").write_text("{}")
    c = _client(sessions, tmp_path)
    listing = c.get("/api/experiments").json()
    assert listing["experiments"][0]["path"] == "exp/1/report.json"
    assert c.get("/api/experiments/report", params={"path": "exp/1/report.json"}).status_code == 200
    assert c.get("/api/experiments/report", params={"path": "../secret.json"}).status_code == 403
    assert c.get("/api/models").json()["models"] == []
    assert c.get("/api/agent").json() == {"recommendations": [], "activity": []}


def test_yields_endpoint(sessions: sessionmaker[Session], tmp_path: Path) -> None:
    from decimal import Decimal  # noqa: PLC0415

    from fundpilot.data.models import CexEarnRateRow  # noqa: PLC0415
    from fundpilot.data.repository import insert_ignore  # noqa: PLC0415

    now = utc_now()
    with session_scope(sessions) as s:
        insert_ignore(
            s,
            CexEarnRateRow,
            [
                {
                    "venue": "okx",
                    "product": "simple_earn_flexible",
                    "ccy": "USDT",
                    "observed_at": now - timedelta(hours=h),
                    "borrow_rate": Decimal("0.04"),
                    "lending_rate": Decimal("0.034"),
                    "origin": "exchange_api",
                }
                for h in range(48)
            ],
        )
    body = _client(sessions, tmp_path).get("/api/yields", params={"days": 7}).json()
    venues = {v["key"]: v for v in body["venues"]}
    assert venues["okx_flexible_USDT"]["n"] == 48
    assert venues["okx_flexible_USDT"]["median"] == pytest.approx(0.034)
    assert body["best_available_by_median"] == "okx_flexible_USDT"
    assert body["min_points_for_ranking"] == 24
    assert venues["bitfinex_fUST_2d"]["available"] is False
    assert len(body["daily"]["okx_flexible_USDT"]) >= 2
