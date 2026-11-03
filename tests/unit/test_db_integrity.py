from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.data.db import session_scope
from fundpilot.data.models import (
    FundingBookLevelRow,
    FundingBookSnapshotRow,
    FundingTradeRow,
)
from tests.conftest import FIXED_NOW


def _trade(**overrides: object) -> FundingTradeRow:
    values: dict[str, object] = dict(
        exchange="bitfinex",
        symbol="fUST",
        trade_id=1,
        executed_at=FIXED_NOW,
        amount=Decimal(1),
        rate=Decimal("0.0002"),
        period_days=2,
    )
    values.update(overrides)
    return FundingTradeRow(**values)


def test_origin_check_constraint(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), session_scope(sessions) as s:
        s.add(_trade(origin="made_up"))


def test_float_rejected_by_exact_decimal(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(StatementError), session_scope(sessions) as s:
        s.add(_trade(amount=1.5))


def test_naive_datetime_rejected(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(StatementError), session_scope(sessions) as s:
        s.add(_trade(executed_at=datetime(2026, 1, 1)))  # noqa: DTZ001


def test_book_levels_cascade_on_snapshot_delete(
    engine: Engine, sessions: sessionmaker[Session]
) -> None:
    with session_scope(sessions) as s:
        s.add(
            FundingBookSnapshotRow(
                exchange="bitfinex",
                symbol="fUST",
                precision="P0",
                captured_at=FIXED_NOW,
                level_count=1,
                levels=[
                    FundingBookLevelRow(
                        side="ask", rate=Decimal("0.0002"), period_days=2, amount=Decimal(10)
                    )
                ],
            )
        )
    with session_scope(sessions) as s:
        s.execute(delete(FundingBookSnapshotRow))
    with session_scope(sessions) as s:
        assert s.scalars(select(FundingBookLevelRow)).all() == []


def test_negative_level_amount_rejected(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), session_scope(sessions) as s:
        s.add(
            FundingBookSnapshotRow(
                exchange="bitfinex",
                symbol="fUST",
                precision="P0",
                captured_at=FIXED_NOW,
                level_count=1,
                levels=[
                    FundingBookLevelRow(
                        side="ask", rate=Decimal("0.0002"), period_days=2, amount=Decimal(-10)
                    )
                ],
            )
        )
