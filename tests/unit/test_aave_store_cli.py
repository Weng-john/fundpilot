from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fundpilot import cli
from fundpilot.config import get_settings
from fundpilot.core.time import utc_now
from fundpilot.data.db import create_db_engine, make_session_factory, session_scope
from fundpilot.defi.aave import store
from fundpilot.defi.aave.math import RateModel, rates
from fundpilot.defi.aave.models import ReserveSnapshot
from tests.conftest import FIXED_NOW

D = Decimal
MODEL = RateModel.from_bps(9000, 0, 550, 3500)
ROOT = Path(__file__).resolve().parents[2]


def _snap(chain: str, t, debt: str = "800000") -> ReserveSnapshot:  # type: ignore[no-untyped-def]
    s_apr, b_apr = rates(MODEL, D(debt), D(1_000_000) - D(debt), D("0.1"))
    return ReserveSnapshot(
        chain=chain,
        symbol="USDC",
        underlying="0x" + "1" * 40,
        observed_at=t,
        total_supplied=D(1_000_000),
        total_debt=D(debt),
        available_liquidity=D(1_000_000) - D(debt),
        supply_apr=s_apr,
        borrow_apr=b_apr,
        reserve_factor=D("0.1"),
        supply_cap=None,
        optimal_usage=MODEL.optimal_usage,
        base_rate=MODEL.base_rate,
        slope1=MODEL.slope1,
        slope2=MODEL.slope2,
    )


def test_store_roundtrip_latest_and_series(sessions: sessionmaker[Session]) -> None:
    snaps = [
        _snap(c, FIXED_NOW + timedelta(hours=h)) for c in ("ethereum", "base") for h in range(5)
    ]
    with session_scope(sessions) as s:
        assert store.save(s, snaps) == 10
        assert store.save(s, snaps) == 0  # idempotent
    with session_scope(sessions) as s:
        last = store.latest(s, ("USDC",))
        grid = store.series(
            s, "USDC", FIXED_NOW, FIXED_NOW + timedelta(hours=4), timedelta(hours=1)
        )
    assert last["base:USDC"].observed_at == FIXED_NOW + timedelta(hours=4)
    assert last["base:USDC"].model == MODEL and last["base:USDC"].supply_apr == snaps[0].supply_apr
    assert len(grid) == 5 and set(grid[0][1]) == {"ethereum:USDC", "base:USDC"}
    # point-in-time: at t only snapshots observed at or before t
    assert all(s.observed_at <= t for t, snaps in grid for s in snaps.values())


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = f"sqlite:///{tmp_path / 'aave.sqlite'}"
    monkeypatch.setenv("FUNDPILOT_DATABASE_URL", url)
    monkeypatch.delenv("FUNDPILOT_AAVE_RPC_URLS", raising=False)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def test_cli_aave_commands(db_env: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["db", "upgrade"]) == 0
    assert cli.main(["aave", "markets"]) == 0
    assert '"symbol": "USDT"' in capsys.readouterr().out
    assert cli.main(["aave", "snapshot"]) == 2  # no RPC URLs configured
    assert cli.main(["aave", "plan", "--symbol", "USDC", "--amount", "1000"]) == 1  # no data
    engine = create_db_engine(db_env)
    with session_scope(make_session_factory(engine)) as s:
        now = utc_now()
        store.save(s, [_snap("ethereum", now), _snap("base", now, debt="880000")])
    engine.dispose()
    capsys.readouterr()
    assert (
        cli.main(["aave", "plan", "--symbol", "USDC", "--amount", "50000", "--chain", "ethereum"])
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["mode"].startswith("DRY-RUN") and out["adopted"]
    assert set(out["target"]) <= {"ethereum:USDC", "base:USDC"}
    assert (
        cli.main(
            [
                "aave",
                "backtest",
                str(ROOT / "experiments" / "aave_usdt.toml"),
                "--synthetic-demo",
                "--output",
                str(tmp_path / "bt"),
            ]
        )
        == 0
    )
    report = json.loads((tmp_path / "bt" / "report.json").read_text())
    assert report["data_label"] == "SYNTHETIC" and len(report["results"]) == 3
    assert cli.main(["aave", "backtest", str(ROOT / "experiments" / "aave_usdc.toml")]) == 1
