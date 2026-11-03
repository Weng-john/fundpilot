from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from fundpilot import cli
from fundpilot.backtesting.experiment import ExperimentConfig, run_experiment, write_report
from fundpilot.backtesting.feed import FeedConfig, MarketFeed
from fundpilot.backtesting.synthetic import SyntheticConfig, generate
from fundpilot.config import get_settings
from fundpilot.core.models import DataOrigin
from tests.conftest import FIXED_NOW

ROOT = Path(__file__).resolve().parents[2]


def _raw(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "name": "t",
        "start": "2026-11-01T12:00:00+00:00",
        "end": "2026-11-01T16:00:00+00:00",
        "step_seconds": 300,
        "scenarios": ["conservative", "optimistic"],
        "baseline": "frr",
        "seed": 1,
        "simulation": {"initial_capital": "5000"},
        "strategies": [
            {"id": "frr", "type": "frr"},
            {"id": "fixed", "type": "fixed_rate", "rate": "0.0002"},
            {"id": "ladder", "type": "adaptive_ladder"},
            {"id": "book", "type": "orderbook_aware", "depth_ahead": "100000"},
            {"id": "renew", "type": "auto_renew"},
        ],
    }
    raw.update(overrides)
    return raw


def test_example_config_parses() -> None:
    cfg = ExperimentConfig.from_toml(ROOT / "experiments" / "baseline_comparison.toml")
    assert cfg.baseline == "frr_2d"
    assert not cfg.allow_synthetic
    assert {s.name for s in cfg.scenarios} == {"conservative", "neutral", "optimistic"}
    for spec in cfg.strategies:
        assert spec.build().name


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        ExperimentConfig.from_dict(_raw(baseline="missing"))
    with pytest.raises(ValueError):
        ExperimentConfig.from_dict(
            _raw(strategies=[{"id": "a", "type": "frr"}, {"id": "a", "type": "frr"}])
        )
    with pytest.raises(ValueError):
        ExperimentConfig.from_dict(_raw(strategies=[{"id": "a", "type": "magic"}])).strategies[
            0
        ].build()


def _feed(cfg: ExperimentConfig) -> MarketFeed:
    t, b, tr = generate(SyntheticConfig(start=FIXED_NOW, days=1, step=timedelta(minutes=5)))
    return MarketFeed(
        FeedConfig(
            exchange="bitfinex",
            symbol="fUST",
            start=cfg.start,
            end=cfg.end,
            step=cfg.step,
            origins=(DataOrigin.SYNTHETIC,),
        ),
        t,
        b,
        tr,
    )


def test_run_experiment_labels_synthetic_and_is_reproducible(tmp_path: Path) -> None:
    cfg = ExperimentConfig.from_dict(_raw())
    feed = _feed(cfg)
    a = run_experiment(cfg, feed, feed.provenance, now=FIXED_NOW)
    b = run_experiment(cfg, feed, feed.provenance, now=FIXED_NOW)
    assert a.to_json() == b.to_json()
    assert a.data_label == "SYNTHETIC"
    assert len(a.runs) == 10
    assert all(r.excess_vs_baseline is None for r in a.runs if r.strategy_id == "frr")
    json_path, md_path = write_report(a, tmp_path)
    data = json.loads(json_path.read_text())
    assert data["kind"] == "simulated" and "SYNTHETIC" in data["warning"]
    assert "NOT MARKET EVIDENCE" in md_path.read_text()


def test_run_experiment_requires_data() -> None:
    cfg = ExperimentConfig.from_dict(_raw())
    empty = MarketFeed(
        FeedConfig(exchange="bitfinex", symbol="fUST", start=cfg.start, end=cfg.end, step=cfg.step),
        [],
        [],
        [],
    )
    with pytest.raises(ValueError, match="stale"):
        run_experiment(cfg, empty, empty.provenance, now=FIXED_NOW)


def test_cli_backtest_synthetic_demo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FUNDPILOT_DATABASE_URL", f"sqlite:///{tmp_path / 'x.sqlite'}")
    get_settings.cache_clear()
    config = tmp_path / "exp.toml"
    config.write_text(
        'name = "cli"\nstart = "2026-11-01T00:00:00+00:00"\nend = "2026-11-01T06:00:00+00:00"\n'
        'step_seconds = 600\nscenarios = ["neutral"]\n'
        '[[strategies]]\nid = "frr"\ntype = "frr"\n'
    )
    try:
        assert (
            cli.main(
                ["backtest", str(config), "--synthetic-demo", "--output", str(tmp_path / "out")]
            )
            == 0
        )
        assert "SYNTHETIC" in capsys.readouterr().out
        assert (tmp_path / "out" / "report.json").exists()
        assert cli.main(["db", "upgrade"]) == 0
        assert cli.main(["demo-data", "--days", "1"]) == 0
        assert '"origin": "synthetic"' in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
