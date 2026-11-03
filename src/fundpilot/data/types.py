"""Portable column types that preserve Decimal precision and UTC awareness.

PostgreSQL stores NUMERIC / TIMESTAMPTZ natively. SQLite (used for tests and
zero-setup local runs) has no exact decimal or tz-aware type, so values are
stored as canonical strings / naive-UTC and restored on load.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Dialect, Numeric, String
from sqlalchemy.types import TypeDecorator, TypeEngine

from fundpilot.core.time import ensure_utc


class ExactDecimal(TypeDecorator[Decimal]):
    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 38, scale: int = 18) -> None:
        super().__init__()
        self.precision = precision
        self.scale = scale

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("float is not allowed for ExactDecimal columns")
        dec = value if isinstance(value, Decimal) else Decimal(value)
        if not dec.is_finite():
            raise ValueError("non-finite decimal")
        if dialect.name == "sqlite":
            return format(dec, "f")
        return dec

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError("UTCDateTime expects datetime")
        aware = ensure_utc(value)
        if dialect.name == "sqlite":
            return aware.replace(tzinfo=None)
        return aware

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        dt: datetime = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
