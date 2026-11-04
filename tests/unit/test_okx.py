from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from fundpilot import cli
from fundpilot.config import get_settings
from fundpilot.core.time import to_ms
from fundpilot.exchanges.errors import ExchangeAPIError, ResponseParseError
from fundpilot.exchanges.okx.earn import OkxEarnClient, parse_history, parse_summary
from fundpilot.exchanges.ratelimit import TokenBucket
from tests.conftest import FIXED_NOW

# shapes copied from live responses on 2026-11-04 (values are illustrative)
SUMMARY = {
    "code": "0",
    "msg": "",
    "data": [
        {
            "avgAmt": "",
            "avgAmtUsd": "",
            "avgRate": "0.0521",
            "ccy": "USDT",
            "estRate": "0.067",
            "preRate": "0.066",
        }
    ],
}


def _hist_row(hours_ago: int) -> dict[str, str]:
    ts = to_ms(FIXED_NOW - timedelta(hours=hours_ago))
    return {"amt": "", "ccy": "USDT", "lendingRate": "0.061", "rate": "0.066", "ts": str(ts)}


def test_parsers() -> None:
    s = parse_summary(SUMMARY)[0]
    assert s.last_borrow_rate == Decimal("0.066") and s.next_borrow_rate_est == Decimal("0.067")
    h = parse_history({"code": "0", "msg": "", "data": [_hist_row(0)]})[0]
    assert h.lending_rate == Decimal("0.061") and h.borrow_rate == Decimal("0.066")
    assert h.observed_at == FIXED_NOW and h.product == "simple_earn_flexible"
    with pytest.raises(ExchangeAPIError):
        parse_history({"code": "50011", "msg": "Too Many Requests", "data": []})
    with pytest.raises(ResponseParseError):
        parse_history({"unexpected": 1})


def test_backfill_paginates_with_after_and_stops_at_start() -> None:
    seen: list[str | None] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["user-agent"].startswith("fundpilot")
        after = req.url.params.get("after")
        seen.append(after)
        first = 0 if after is None else (to_ms(FIXED_NOW) - int(after)) // 3_600_000 + 1
        rows = [_hist_row(h) for h in range(first, first + 100)]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    client = OkxEarnClient(
        "https://okx.invalid",
        transport=httpx.MockTransport(handler),
        limiter=TokenBucket(6000, burst=100),
    )
    rows = client.backfill("USDT", FIXED_NOW - timedelta(hours=250))
    assert len(rows) == 251  # hours 0..250 inclusive
    assert rows[0].observed_at == FIXED_NOW - timedelta(hours=250)
    assert rows[-1].observed_at == FIXED_NOW
    assert seen[0] is None and len(seen) == 3


def test_http_error_raises() -> None:
    client = OkxEarnClient(
        "https://okx.invalid",
        transport=httpx.MockTransport(lambda r: httpx.Response(403)),
        limiter=TokenBucket(6000, burst=10),
    )
    with pytest.raises(ExchangeAPIError):
        client.summary("USDT")


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = f"sqlite:///{tmp_path / 'okx.sqlite'}"
    monkeypatch.setenv("FUNDPILOT_DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def test_compare_yields_runs_on_empty_db(db_env: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["db", "upgrade"]) == 0
    capsys.readouterr()
    assert cli.main(["compare-yields", "--days", "7"]) == 0
    out = json.loads(capsys.readouterr().out)
    venues = {v["key"]: v for v in out["venues"]}
    assert venues["okx_flexible_USDT"]["n"] == 0
    assert venues["bitfinex_fUST_2d"]["available"] is False
    assert "Taiwan" in venues["bitfinex_fUST_2d"]["note"]
    assert out["best_available_by_median"] is None


def test_csv_collect_and_import_roundtrip(tmp_path: Path) -> None:
    from fundpilot.data.db import create_db_engine, make_session_factory, session_scope  # noqa: PLC0415
    from fundpilot.data.models import Base, CexEarnRateRow  # noqa: PLC0415
    from fundpilot.yields.csvstore import collect_okx, import_dir  # noqa: PLC0415

    def handler(req: httpx.Request) -> httpx.Response:
        after = req.url.params.get("after")
        rows = [] if after else [_hist_row(h) for h in range(3)]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    client = OkxEarnClient(
        "https://okx.invalid",
        transport=httpx.MockTransport(handler),
        limiter=TokenBucket(6000, burst=100),
    )
    # window relative to wall clock: use a huge window so the fixed fixture times are inside
    assert collect_okx(tmp_path, hours=24 * 365 * 5, ccys=("USDT",), client=client) == {"USDT": 3}
    assert collect_okx(tmp_path, hours=24 * 365 * 5, ccys=("USDT",), client=client) == {"USDT": 0}
    engine = create_db_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with session_scope(sessions) as s:
        assert import_dir(s, tmp_path) == {"okx_flexible_USDT.csv": 3}
        assert import_dir(s, tmp_path) == {"okx_flexible_USDT.csv": 0}
        assert s.query(CexEarnRateRow).count() == 3
    engine.dispose()
