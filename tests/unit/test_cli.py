from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from fundpilot import cli
from fundpilot.config import ExecutionMode, Settings, get_settings


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = f"sqlite:///{tmp_path / 'cli.sqlite'}"
    monkeypatch.setenv("FUNDPILOT_DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def test_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.execution_mode is ExecutionMode.DRY_RUN
    assert not any("key" in name or "secret" in name for name in Settings.model_fields)


def test_db_upgrade_status_and_prune(db_env: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["db", "upgrade"]) == 0
    assert cli.main(["status"]) == 0
    assert '"execution_mode": "dry_run"' in capsys.readouterr().out
    assert cli.main(["prune", "funding_trades", "--older-than-days", "30"]) == 0
    captured = capsys.readouterr()
    assert '"dry_run": true' in captured.out
    assert "dry run only" in captured.err
    assert cli.main(["quality"]) == 0


def test_parser_requires_command() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_operational_commands(db_env: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["db", "upgrade"]) == 0
    assert cli.main(["emergency-stop", "--reason", "drill"]) == 0
    assert "cannot be recalled" in capsys.readouterr().out
    assert cli.main(["alerts"]) == 0
    assert "emergency_stop" in capsys.readouterr().out
    assert cli.main(["resume"]) == 2  # needs --confirm
    assert cli.main(["resume", "--confirm"]) == 0
    assert cli.main(["approvals", "list"]) == 0
    assert cli.main(["approvals", "approve"]) == 2
    assert cli.main(["paper", "status", "--account", "nope"]) == 1
