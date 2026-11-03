"""Deterministic analytics used by reports and exposed to the agent as read-only tools."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from fundpilot.data.models import (
    AlertRow,
    DataQualityIssue,
    FundingBookSnapshotRow,
    FundingTickerRow,
    FundingTradeRow,
    FundingWalletSnapshotRow,
    IngestionRun,
)

DAYS = Decimal(365)


def _f(x: Decimal | None) -> float | None:
    return None if x is None else float(x)


def jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        obj = asdict(obj)
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


@dataclass(frozen=True, slots=True)
class MarketSummary:
    symbol: str
    as_of: datetime
    data_origin: str
    last_ticker_at: datetime | None
    frr_daily: float | None
    frr_apr: float | None  # simple, gross
    frr_net_apr_after_fee: float | None  # simple, after the assumed lender fee
    frr_24h_mean: float | None
    frr_24h_min: float | None
    frr_24h_max: float | None
    frr_7d_percentile: float | None
    best_bid: float | None
    best_ask: float | None
    ask_depth: float | None
    bid_depth: float | None
    trades_24h: int
    volume_24h: float
    vwap_24h: float | None
    note: str = "Rates are daily fractions; APR = daily x 365 (simple, no compounding)."


def market_summary(
    session: Session,
    symbol: str,
    now: datetime,
    *,
    exchange: str = "bitfinex",
    lender_fee: Decimal = Decimal("0.15"),
    origins: tuple[str, ...] = ("exchange_api",),
) -> MarketSummary:
    tickers = session.execute(
        select(FundingTickerRow.observed_at, FundingTickerRow.frr)
        .where(
            FundingTickerRow.exchange == exchange,
            FundingTickerRow.symbol == symbol,
            FundingTickerRow.observed_at > now - timedelta(days=7),
            FundingTickerRow.observed_at <= now,
            FundingTickerRow.origin.in_(origins),
        )
        .order_by(FundingTickerRow.observed_at)
    ).all()
    frrs_7d = [float(f) for _, f in tickers if f is not None]
    last = tickers[-1] if tickers else None
    frr = float(last[1]) if last and last[1] is not None else None
    day = [float(f) for t, f in tickers if f is not None and t > now - timedelta(days=1)]
    pct = (
        (sum(1 for x in frrs_7d if x <= frr) / len(frrs_7d))
        if frr is not None and frrs_7d
        else None
    )
    book = session.scalars(
        select(FundingBookSnapshotRow)
        .options(selectinload(FundingBookSnapshotRow.levels))
        .where(
            FundingBookSnapshotRow.exchange == exchange,
            FundingBookSnapshotRow.symbol == symbol,
            FundingBookSnapshotRow.captured_at <= now,
            FundingBookSnapshotRow.origin.in_(origins),
        )
        .order_by(FundingBookSnapshotRow.captured_at.desc())
        .limit(1)
    ).first()
    bids = [lv for lv in book.levels if lv.side == "bid"] if book else []
    asks = [lv for lv in book.levels if lv.side == "ask"] if book else []
    trades = session.execute(
        select(FundingTradeRow.amount, FundingTradeRow.rate).where(
            FundingTradeRow.exchange == exchange,
            FundingTradeRow.symbol == symbol,
            FundingTradeRow.executed_at > now - timedelta(days=1),
            FundingTradeRow.executed_at <= now,
            FundingTradeRow.origin.in_(origins),
        )
    ).all()
    vol = sum(abs(float(a)) for a, _ in trades)
    vwap = sum(abs(float(a)) * float(r) for a, r in trades) / vol if vol else None
    fee = float(lender_fee)
    return MarketSummary(
        symbol=symbol,
        as_of=now,
        data_origin=",".join(origins),
        last_ticker_at=last[0] if last else None,
        frr_daily=frr,
        frr_apr=None if frr is None else frr * 365,
        frr_net_apr_after_fee=None if frr is None else frr * 365 * (1 - fee),
        frr_24h_mean=sum(day) / len(day) if day else None,
        frr_24h_min=min(day) if day else None,
        frr_24h_max=max(day) if day else None,
        frr_7d_percentile=pct,
        best_bid=max((float(lv.rate) for lv in bids), default=None),
        best_ask=min((float(lv.rate) for lv in asks), default=None),
        ask_depth=sum(float(lv.amount) for lv in asks) if asks else None,
        bid_depth=sum(float(lv.amount) for lv in bids) if bids else None,
        trades_24h=len(trades),
        volume_24h=vol,
        vwap_24h=vwap,
    )


@dataclass(frozen=True, slots=True)
class Anomaly:
    at: datetime
    frr: float
    zscore: float
    kind: str  # "spike" | "drop"


def rate_anomalies(
    session: Session,
    symbol: str,
    now: datetime,
    *,
    lookback: timedelta = timedelta(days=7),
    z_threshold: float = 3.0,
    baseline: timedelta = timedelta(days=1),
    exchange: str = "bitfinex",
    origins: tuple[str, ...] = ("exchange_api",),
) -> list[Anomaly]:
    """FRR observations deviating more than ``z_threshold`` std from the trailing ``baseline``."""
    rows = session.execute(
        select(FundingTickerRow.observed_at, FundingTickerRow.frr)
        .where(
            FundingTickerRow.exchange == exchange,
            FundingTickerRow.symbol == symbol,
            FundingTickerRow.observed_at > now - lookback - baseline,
            FundingTickerRow.observed_at <= now,
            FundingTickerRow.origin.in_(origins),
            FundingTickerRow.frr.is_not(None),
        )
        .order_by(FundingTickerRow.observed_at)
    ).all()
    series = [(t, float(f)) for t, f in rows if f is not None]
    out: list[Anomaly] = []
    lo = 0
    for i, (t, v) in enumerate(series):
        while series[lo][0] < t - baseline:
            lo += 1
        window = [x for _, x in series[lo:i]]
        if t <= now - lookback or len(window) < 10:
            continue
        mean = sum(window) / len(window)
        std = math.sqrt(sum((x - mean) ** 2 for x in window) / (len(window) - 1))
        if std > 0 and abs(v - mean) / std >= z_threshold:
            out.append(Anomaly(t, v, (v - mean) / std, "spike" if v > mean else "drop"))
    return out


@dataclass(frozen=True, slots=True)
class UtilizationReview:
    account: str
    mode: str
    label: str
    snapshots: int
    first_equity: float | None
    last_equity: float | None
    avg_idle_share: float | None
    max_idle_share: float | None
    unsettled_interest: float | None


def utilization_review(
    session: Session, account: str, mode: str, now: datetime, days: int = 7
) -> UtilizationReview:
    rows = session.execute(
        select(
            FundingWalletSnapshotRow.balance,
            FundingWalletSnapshotRow.available,
            FundingWalletSnapshotRow.unsettled_interest,
        )
        .where(
            FundingWalletSnapshotRow.account == account,
            FundingWalletSnapshotRow.mode == mode,
            FundingWalletSnapshotRow.captured_at > now - timedelta(days=days),
            FundingWalletSnapshotRow.captured_at <= now,
        )
        .order_by(FundingWalletSnapshotRow.captured_at)
    ).all()
    idle = [float(a) / float(b) for b, a, _ in rows if b and a is not None and b > 0]
    label = "SIMULATED (paper trading)" if mode == "paper" else "REALISED (live account)"
    return UtilizationReview(
        account=account,
        mode=mode,
        label=label,
        snapshots=len(rows),
        first_equity=_f(rows[0][0]) if rows else None,
        last_equity=_f(rows[-1][0]) if rows else None,
        avg_idle_share=sum(idle) / len(idle) if idle else None,
        max_idle_share=max(idle) if idle else None,
        unsettled_interest=_f(rows[-1][2]) if rows else None,
    )


@dataclass(frozen=True, slots=True)
class OperationalReview:
    window_hours: int
    ingestion_runs: int
    ingestion_failures: int
    failures_by_job: dict[str, int]
    quality_issues: dict[str, int]
    alerts: dict[str, int]
    critical_alerts: list[str]


def operational_review(session: Session, now: datetime, hours: int = 24) -> OperationalReview:
    since = now - timedelta(hours=hours)
    runs = session.execute(
        select(IngestionRun.job, IngestionRun.status).where(
            IngestionRun.started_at > since, IngestionRun.started_at <= now
        )
    ).all()
    failures: dict[str, int] = {}
    for job, status in runs:
        if status == "failed":
            failures[job] = failures.get(job, 0) + 1
    dq = dict(
        session.execute(
            select(DataQualityIssue.kind, func.count())
            .where(DataQualityIssue.detected_at > since)
            .group_by(DataQualityIssue.kind)
        ).all()
    )
    alerts = dict(
        session.execute(
            select(AlertRow.code, func.count()).where(AlertRow.at > since).group_by(AlertRow.code)
        ).all()
    )
    critical = list(
        session.scalars(
            select(AlertRow.message)
            .where(AlertRow.at > since, AlertRow.severity == "critical")
            .order_by(AlertRow.at.desc())
            .limit(5)
        )
    )
    return OperationalReview(
        hours,
        len(runs),
        sum(failures.values()),
        failures,
        {str(k): int(v) for k, v in dq.items()},
        {str(k): int(v) for k, v in alerts.items()},
        critical,
    )


def experiment_summary(path: Path, allowed_root: Path) -> dict[str, Any]:
    """Summarise a backtest/RL/ML report JSON. Only files under ``allowed_root`` are readable."""
    resolved = path.resolve()
    root = allowed_root.resolve()
    if root not in resolved.parents:
        raise PermissionError(f"{path} is outside {allowed_root}")
    data: dict[str, Any] = json.loads(resolved.read_text())
    summary: dict[str, Any] = {
        k: data.get(k)
        for k in (
            "kind",
            "data_label",
            "warning",
            "experiment",
            "task",
            "window",
            "train_window",
            "test_window",
            "baseline",
            "label_source",
            "label_warning",
        )
        if k in data
    }
    runs = data.get("runs") or data.get("backtests") or []
    best: dict[str, Any] = {}
    for r in runs:
        scen = r["scenario"]
        strat = r.get("strategy_id") or r.get("strategy")
        y = float(
            r["metrics"]["net_annualized_yield_after_costs"]
            if "net_annualized_yield_after_costs" in r["metrics"]
            else r["metrics"]["net_annualized_yield"]
        )
        if scen not in best or y > best[scen]["net_annualized_yield"]:
            best[scen] = {
                "strategy": strat,
                "net_annualized_yield": y,
                "excess_vs_baseline": r.get("excess_vs_baseline"),
            }
    if best:
        summary["best_by_scenario"] = best
    if "models" in data:
        summary["models"] = {
            name: {k: v for k, v in m.items() if k != "reliability"}
            for name, m in data["models"].items()
        }
    return summary
