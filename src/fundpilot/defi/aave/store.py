"""Persist and load Aave reserve snapshots."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Collection, Iterable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundpilot.core.models import DataOrigin
from fundpilot.core.time import ensure_utc
from fundpilot.data.models import DefiReserveSnapshotRow
from fundpilot.data.repository import insert_ignore
from fundpilot.defi.aave.models import ReserveSnapshot


def save(session: Session, snapshots: Iterable[ReserveSnapshot]) -> int:
    rows = [s.model_dump() | {"origin": s.origin.value} for s in snapshots]
    return insert_ignore(session, DefiReserveSnapshotRow, rows)


def _to_model(r: DefiReserveSnapshotRow) -> ReserveSnapshot:
    fields = {c: getattr(r, c) for c in ReserveSnapshot.model_fields if c != "origin"}
    return ReserveSnapshot(**fields, origin=DataOrigin(r.origin))


def latest(
    session: Session,
    symbols: Collection[str] = ("USDT", "USDC"),
    origins: Collection[DataOrigin] = (DataOrigin.EXCHANGE_API,),
    max_age: timedelta | None = None,
    now: datetime | None = None,
) -> dict[str, ReserveSnapshot]:
    rows = session.scalars(
        select(DefiReserveSnapshotRow)
        .where(
            DefiReserveSnapshotRow.symbol.in_(list(symbols)),
            DefiReserveSnapshotRow.origin.in_([o.value for o in origins]),
        )
        .order_by(DefiReserveSnapshotRow.observed_at)
    ).all()
    out: dict[str, ReserveSnapshot] = {}
    for r in rows:
        s = _to_model(r)
        out[s.key] = s
    if max_age is not None and now is not None:
        out = {k: s for k, s in out.items() if ensure_utc(now) - s.observed_at <= max_age}
    return out


def series(
    session: Session,
    symbol: str,
    start: datetime,
    end: datetime,
    step: timedelta,
    origins: Collection[DataOrigin] = (DataOrigin.EXCHANGE_API,),
    max_staleness: timedelta = timedelta(hours=2),
) -> list[tuple[datetime, dict[str, ReserveSnapshot]]]:
    """Point-in-time grid: at each ``t`` the latest snapshot per market observed at or before
    ``t`` (and not older than ``max_staleness``)."""
    rows = session.scalars(
        select(DefiReserveSnapshotRow)
        .where(
            DefiReserveSnapshotRow.symbol == symbol,
            DefiReserveSnapshotRow.observed_at <= ensure_utc(end),
            DefiReserveSnapshotRow.observed_at >= ensure_utc(start) - max_staleness,
            DefiReserveSnapshotRow.origin.in_([o.value for o in origins]),
        )
        .order_by(DefiReserveSnapshotRow.observed_at)
    ).all()
    by_key: dict[str, list[ReserveSnapshot]] = {}
    for r in rows:
        s = _to_model(r)
        by_key.setdefault(s.key, []).append(s)
    times = {k: [s.observed_at for s in v] for k, v in by_key.items()}
    out = []
    t = ensure_utc(start)
    while t <= ensure_utc(end):
        snaps = {}
        for k, items in by_key.items():
            i = bisect_right(times[k], t)
            if i and t - items[i - 1].observed_at <= max_staleness:
                snaps[k] = items[i - 1]
        if snaps:
            out.append((t, snaps))
        t += step
    return out
