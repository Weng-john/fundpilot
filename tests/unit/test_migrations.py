from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from fundpilot.data.db import create_db_engine
from fundpilot.data.migrate import upgrade
from fundpilot.data.models import Base


def test_migrations_match_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.sqlite'}"
    upgrade(url)
    engine = create_db_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
        tables = set(inspect(conn).get_table_names())
    engine.dispose()
    assert diff == [], diff
    assert set(Base.metadata.tables) <= tables
