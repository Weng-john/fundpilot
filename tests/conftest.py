from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.data.db import create_db_engine, make_session_factory
from fundpilot.data.models import Base

FIXTURES = Path(__file__).parent / "fixtures" / "bitfinex"
FIXED_NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(), parse_float=Decimal)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_db_engine(f"sqlite:///{tmp_path / 'test.sqlite'}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)
