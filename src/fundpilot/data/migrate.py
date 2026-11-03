"""Programmatic Alembic upgrade (used by the CLI and tests)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

_ROOT = Path(__file__).resolve().parents[3]


def alembic_config(database_url: str) -> Config:
    ini = _ROOT / "alembic.ini"
    cfg = Config(str(ini)) if ini.exists() else Config()
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    cfg.attributes["url"] = database_url
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    """Revert migrations. Destructive: drops tables/data above ``revision``."""
    command.downgrade(alembic_config(database_url), revision)
