"""Configurable data retention. Deletion is destructive: callers default to dry-run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from fundpilot.core.time import ensure_utc
from fundpilot.data.models import DataQualityIssue
from fundpilot.data.repository import TIME_COLUMNS

PRUNABLE: dict[str, tuple[Any, InstrumentedAttribute[datetime]]] = {
    **TIME_COLUMNS,
    "data_quality_issues": (DataQualityIssue, DataQualityIssue.detected_at),
}


@dataclass(frozen=True, slots=True)
class PruneResult:
    dataset: str
    cutoff: datetime
    matched: int
    deleted: int
    dry_run: bool


def prune(session: Session, dataset: str, cutoff: datetime, *, dry_run: bool = True) -> PruneResult:
    """Delete rows of ``dataset`` older than ``cutoff``. Only executes when ``dry_run=False``."""
    if dataset not in PRUNABLE:
        raise ValueError(f"unknown dataset {dataset!r}; choose from {sorted(PRUNABLE)}")
    cutoff = ensure_utc(cutoff)
    model, column = PRUNABLE[dataset]
    matched = session.scalar(select(func.count()).select_from(model).where(column < cutoff)) or 0
    deleted = 0
    if not dry_run and matched:
        result = session.execute(delete(model).where(column < cutoff))
        deleted = max(result.rowcount or 0, 0)  # type: ignore[attr-defined]
    return PruneResult(dataset, cutoff, matched, deleted, dry_run)
