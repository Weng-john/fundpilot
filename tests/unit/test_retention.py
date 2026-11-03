from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.data import repository, retention
from fundpilot.data.db import session_scope
from tests.conftest import FIXED_NOW
from tests.fakes import make_trades


def test_prune_dry_run_then_delete(sessions: sessionmaker[Session]) -> None:
    with session_scope(sessions) as s:
        repository.save_trades(
            s, make_trades(10, FIXED_NOW - timedelta(days=10), timedelta(days=1))
        )
    cutoff = FIXED_NOW - timedelta(days=5)
    with session_scope(sessions) as s:
        dry = retention.prune(s, "funding_trades", cutoff)
        assert (dry.matched, dry.deleted, dry.dry_run) == (5, 0, True)
    with session_scope(sessions) as s:
        assert repository.row_counts(s)["funding_trades"] == 10
        real = retention.prune(s, "funding_trades", cutoff, dry_run=False)
        assert real.deleted == 5
    with session_scope(sessions) as s:
        assert repository.row_counts(s)["funding_trades"] == 5


def test_prune_unknown_dataset(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError), session_scope(sessions) as s:
        retention.prune(s, "users", FIXED_NOW)
