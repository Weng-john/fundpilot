"""FastAPI application serving dashboard data.

* Every response that contains account or performance figures carries a ``label``:
  ``SIMULATED`` (paper/backtest), ``REALISED`` (live) or ``FORECAST``.
* Market endpoints use only ``origin=exchange_api`` rows unless ``include_synthetic=true``.
* The only write endpoint is ``POST /api/controls/emergency-stop`` (reduces risk). It requires
  ``FUNDPILOT_API_TOKEN``; without it the endpoint is disabled. Resuming is CLI-only.
"""

import hmac
import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from fundpilot import __version__
from fundpilot.agents import analytics
from fundpilot.agents.supervisor import recent_activity
from fundpilot.config import Settings, get_settings
from fundpilot.core.time import utc_now
from fundpilot.data.db import create_db_engine, make_session_factory, session_scope
from fundpilot.data.models import (
    AgentRecommendationRow,
    AlertRow,
    ApprovalRequestRow,
    ExecutionAuditRow,
    FundingBookSnapshotRow,
    FundingStatRow,
    FundingTickerRow,
    FundingTradeRow,
    FundingWalletSnapshotRow,
    PaperAccountStateRow,
)
from fundpilot.execution.controls import Controls
from fundpilot.risk.manager import CREDITS_NOT_RECALLABLE

MAX_POINTS = 2000


class EmergencyStopRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    by: str = Field(default="dashboard", max_length=64)


def create_app(
    settings: Settings | None = None, sessions: sessionmaker[Session] | None = None
) -> FastAPI:
    settings = settings or get_settings()
    if sessions is None:
        sessions = make_session_factory(create_db_engine(settings.database_url))
    factory = sessions
    app = FastAPI(title="FundPilot dashboard API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    def db() -> Iterator[Session]:
        with session_scope(factory) as s:
            yield s

    DB = Annotated[Session, Depends(db)]

    def origins(include_synthetic: bool) -> tuple[str, ...]:
        return ("exchange_api", "synthetic") if include_synthetic else ("exchange_api",)

    # -- health / overview -------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        stop, reason = Controls(factory).emergency_state()
        return {
            "status": "ok",
            "version": __version__,
            "execution_mode": settings.execution_mode.value,
            "emergency_stop": stop,
            "emergency_reason": reason,
            "write_api_enabled": settings.api_token is not None,
        }

    @app.get("/api/overview")
    def overview(
        s: DB, symbol: str = "fUST", account: str | None = None, include_synthetic: bool = False
    ) -> dict[str, Any]:
        now = utc_now()
        out: dict[str, Any] = {
            "market": analytics.jsonable(
                analytics.market_summary(s, symbol, now, origins=origins(include_synthetic))
            ),
            "accounts": list(s.scalars(select(PaperAccountStateRow.account))),
        }
        if account:
            out["account"] = _paper_account(s, account)
        return out

    # -- market ------------------------------------------------------------------
    @app.get("/api/market/frr")
    def frr_series(
        s: DB,
        symbol: str = "fUST",
        hours: int = Query(168, ge=1, le=24 * 365),
        include_synthetic: bool = False,
    ) -> dict[str, Any]:
        rows = s.execute(
            select(FundingTickerRow.observed_at, FundingTickerRow.frr)
            .where(
                FundingTickerRow.symbol == symbol,
                FundingTickerRow.observed_at > utc_now() - timedelta(hours=hours),
                FundingTickerRow.origin.in_(origins(include_synthetic)),
                FundingTickerRow.frr.is_not(None),
            )
            .order_by(FundingTickerRow.observed_at)
        ).all()
        step = max(1, len(rows) // MAX_POINTS)
        return {
            "symbol": symbol,
            "unit": "daily fraction",
            "points": [{"t": t.isoformat(), "frr": str(v)} for t, v in rows[::step]],
        }

    @app.get("/api/market/stats")
    def funding_stats(
        s: DB,
        symbol: str = "fUST",
        days: int = Query(30, ge=1, le=365),
        include_synthetic: bool = False,
    ) -> dict[str, Any]:
        rows = s.scalars(
            select(FundingStatRow)
            .where(
                FundingStatRow.symbol == symbol,
                FundingStatRow.observed_at > utc_now() - timedelta(days=days),
                FundingStatRow.origin.in_(origins(include_synthetic)),
            )
            .order_by(FundingStatRow.observed_at)
        ).all()
        step = max(1, len(rows) // MAX_POINTS)
        return {
            "symbol": symbol,
            "points": [
                {
                    "t": r.observed_at.isoformat(),
                    "frr": _s(r.frr),
                    "frr_raw": _s(r.frr_raw),
                    "funding_amount": _s(r.funding_amount),
                    "funding_amount_used": _s(r.funding_amount_used),
                    "avg_period_days": _s(r.avg_period_days),
                }
                for r in rows[::step]
            ],
        }

    @app.get("/api/market/book")
    def book(s: DB, symbol: str = "fUST", include_synthetic: bool = False) -> dict[str, Any]:
        snap = s.scalars(
            select(FundingBookSnapshotRow)
            .options(selectinload(FundingBookSnapshotRow.levels))
            .where(
                FundingBookSnapshotRow.symbol == symbol,
                FundingBookSnapshotRow.precision == "P0",
                FundingBookSnapshotRow.origin.in_(origins(include_synthetic)),
            )
            .order_by(FundingBookSnapshotRow.captured_at.desc())
            .limit(1)
        ).first()
        if snap is None:
            return {"symbol": symbol, "captured_at": None, "levels": []}
        return {
            "symbol": symbol,
            "captured_at": snap.captured_at.isoformat(),
            "origin": snap.origin,
            "levels": [
                {
                    "side": lv.side,
                    "rate": str(lv.rate),
                    "period_days": lv.period_days,
                    "amount": str(lv.amount),
                    "count": lv.count,
                }
                for lv in sorted(snap.levels, key=lambda lv: (lv.side, lv.rate))
            ],
        }

    @app.get("/api/market/rate-distribution")
    def rate_distribution(
        s: DB,
        symbol: str = "fUST",
        hours: int = Query(24, ge=1, le=720),
        bins: int = Query(20, ge=2, le=100),
        include_synthetic: bool = False,
    ) -> dict[str, Any]:
        rows = s.execute(
            select(FundingTradeRow.rate, FundingTradeRow.amount).where(
                FundingTradeRow.symbol == symbol,
                FundingTradeRow.executed_at > utc_now() - timedelta(hours=hours),
                FundingTradeRow.origin.in_(origins(include_synthetic)),
            )
        ).all()
        if not rows:
            return {"symbol": symbol, "bins": []}
        rates = [float(r) for r, _ in rows]
        lo, hi = min(rates), max(rates)
        width = (hi - lo) / bins or 1e-12
        hist = [
            {"low": lo + i * width, "high": lo + (i + 1) * width, "volume": 0.0, "count": 0}
            for i in range(bins)
        ]
        for (_, amount), r in zip(rows, rates, strict=True):
            i = min(bins - 1, int((r - lo) / width))
            hist[i]["volume"] += abs(float(amount))
            hist[i]["count"] += 1
        return {"symbol": symbol, "hours": hours, "bins": hist}

    # -- strategy / experiments ------------------------------------------------------
    reports_root = Path(settings.api_reports_root)

    @app.get("/api/experiments")
    def experiments() -> dict[str, Any]:
        items = []
        if reports_root.exists():
            for path in sorted(reports_root.rglob("report.json"), reverse=True)[:200]:
                try:
                    summary = analytics.experiment_summary(path, reports_root)
                except (OSError, ValueError, KeyError, PermissionError):
                    continue
                items.append({"path": str(path.relative_to(reports_root)), **summary})
        return {"label": "SIMULATED", "experiments": items}

    @app.get("/api/experiments/report")
    def experiment(path: str) -> dict[str, Any]:
        target = reports_root / path
        try:
            resolved = target.resolve()
            if reports_root.resolve() not in resolved.parents:
                raise PermissionError(path)
            data: dict[str, Any] = json.loads(resolved.read_text())
        except PermissionError as exc:
            raise HTTPException(403, "path outside the reports directory") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "report not found") from exc
        return data

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        root = Path(settings.api_models_root)
        out = []
        if root.exists():
            for meta_path in sorted(root.glob("*/*/metadata.json")):
                meta = json.loads(meta_path.read_text())
                out.append(
                    {
                        k: meta.get(k)
                        for k in (
                            "task",
                            "version",
                            "created_at",
                            "model_type",
                            "training_window",
                            "label_source",
                            "rows",
                        )
                    }
                    | {
                        "evaluation": {
                            name: {k: v for k, v in m.items() if k != "reliability"}
                            for name, m in meta.get("out_of_sample_evaluation", {})
                            .get("models", {})
                            .items()
                        },
                        "top_features": list(meta.get("feature_importance_gain", {}))[:10],
                    }
                )
        return {"label": "FORECAST models (out-of-sample metrics)", "models": out}

    # -- risk / execution ----------------------------------------------------------------
    @app.get("/api/risk")
    def risk(s: DB, account: str | None = None) -> dict[str, Any]:
        stop, reason = Controls(factory).emergency_state()
        out: dict[str, Any] = {
            "emergency_stop": stop,
            "emergency_reason": reason,
            "credits_note": CREDITS_NOT_RECALLABLE,
            "alerts": [
                {
                    "at": a.at.isoformat(),
                    "code": a.code,
                    "severity": a.severity,
                    "message": a.message,
                    "source": a.source,
                }
                for a in s.scalars(select(AlertRow).order_by(AlertRow.at.desc()).limit(50))
            ],
            "pending_approvals": [
                {
                    "id": r.id,
                    "account": r.account,
                    "payload": r.payload,
                    "expires_at": r.expires_at.isoformat(),
                }
                for r in s.scalars(
                    select(ApprovalRequestRow).where(ApprovalRequestRow.status == "pending")
                )
            ],
        }
        if account:
            out["account"] = _paper_account(s, account)
        return out

    @app.get("/api/execution")
    def execution(
        s: DB, account: str | None = None, limit: int = Query(100, le=500)
    ) -> dict[str, Any]:
        stmt = select(ExecutionAuditRow).order_by(ExecutionAuditRow.at.desc()).limit(limit)
        counts_stmt = select(ExecutionAuditRow.outcome, func.count()).group_by(
            ExecutionAuditRow.outcome
        )
        if account:
            stmt = stmt.where(ExecutionAuditRow.account == account)
            counts_stmt = counts_stmt.where(ExecutionAuditRow.account == account)
        return {
            "counts": {k: int(v) for k, v in s.execute(counts_stmt).all()},
            "recent": [
                {
                    "at": r.at.isoformat(),
                    "mode": r.mode,
                    "action": r.action,
                    "outcome": r.outcome,
                    "payload": r.payload,
                    "detail": r.detail,
                }
                for r in s.scalars(stmt)
            ],
        }

    @app.get("/api/accounts/{account}/equity")
    def equity(
        s: DB, account: str, mode: str = "paper", days: int = Query(30, le=365)
    ) -> dict[str, Any]:
        rows = s.execute(
            select(
                FundingWalletSnapshotRow.captured_at,
                FundingWalletSnapshotRow.balance,
                FundingWalletSnapshotRow.available,
            )
            .where(
                FundingWalletSnapshotRow.account == account,
                FundingWalletSnapshotRow.mode == mode,
                FundingWalletSnapshotRow.captured_at > utc_now() - timedelta(days=days),
            )
            .order_by(FundingWalletSnapshotRow.captured_at)
        ).all()
        step = max(1, len(rows) // MAX_POINTS)
        return {
            "account": account,
            "label": _label(mode),
            "points": [
                {"t": t.isoformat(), "equity": str(b), "available": _s(a)}
                for t, b, a in rows[::step]
            ],
        }

    # -- agent ---------------------------------------------------------------------------
    @app.get("/api/agent")
    def agent(s: DB) -> dict[str, Any]:
        recs = s.scalars(
            select(AgentRecommendationRow).order_by(AgentRecommendationRow.id.desc()).limit(50)
        )
        return {
            "recommendations": [
                {
                    "id": r.id,
                    "parameter": r.parameter,
                    "value": r.proposed_value,
                    "rationale": r.rationale,
                    "risk_increasing": r.risk_increasing,
                    "status": r.status,
                    "source": r.source,
                    "created_at": r.created_at.isoformat(),
                }
                for r in recs
            ],
            "activity": [
                {"at": a.at.isoformat(), "kind": a.kind, "summary": a.summary}
                for a in recent_activity(factory)[:50]
            ],
        }

    # -- controls (write) -----------------------------------------------------------------
    @app.post("/api/controls/emergency-stop")
    def emergency_stop(
        body: EmergencyStopRequest, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        token = settings.api_token.get_secret_value() if settings.api_token else None
        if token is None:
            raise HTTPException(403, "write API disabled: set FUNDPILOT_API_TOKEN")
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, token):
            raise HTTPException(401, "invalid token")
        message = Controls(factory).emergency_stop(body.reason, by=body.by)
        return {"emergency_stop": True, "message": message}

    return app


def _s(x: Decimal | None) -> str | None:
    return None if x is None else str(x)


def _label(mode: str) -> str:
    return "SIMULATED" if mode == "paper" else "REALISED"


def _paper_account(s: Session, account: str) -> dict[str, Any] | None:
    row = s.get(PaperAccountStateRow, account)
    if row is None:
        return None
    st = row.state
    now = utc_now()
    credits = st["credits"]
    lent = sum((Decimal(c["amount"]) for c in credits), Decimal(0))
    offered = sum((Decimal(o["remaining"]) for o in st["offers"]), Decimal(0))
    available = Decimal(st["available"])
    total = available + offered + lent
    maturity: dict[str, Decimal] = {}
    by_period: dict[str, Decimal] = {}
    for c in credits:
        days = (datetime.fromisoformat(c["expires_at"]) - now).total_seconds() / 86_400
        bucket = (
            "<1d"
            if days < 1
            else "1-2d"
            if days < 2
            else "2-7d"
            if days < 7
            else "7-30d"
            if days < 30
            else ">30d"
        )
        maturity[bucket] = maturity.get(bucket, Decimal(0)) + Decimal(c["amount"])
        key = f"{c['period_days']}d"
        by_period[key] = by_period.get(key, Decimal(0)) + Decimal(c["amount"])
    rates = [(Decimal(c["rate"]), Decimal(c["amount"])) for c in credits]
    avg_rate = sum((r * a for r, a in rates), Decimal(0)) / lent if lent else None
    return {
        "account": account,
        "label": "SIMULATED (paper trading)",
        "updated_at": row.updated_at.isoformat(),
        "total": str(total),
        "available": str(available),
        "offered": str(offered),
        "lent": str(lent),
        "utilization": str(lent / total) if total else None,
        "active_credits": len(credits),
        "open_offers": len(st["offers"]),
        "gross_interest": st["gross"],
        "fees": st["fees"],
        "paid_interest": st["paid"],
        "pending_interest": st["pending"],
        "weighted_credit_rate_daily": None if avg_rate is None else str(avg_rate),
        "weighted_credit_apr": None if avg_rate is None else str(avg_rate * 365),
        "maturity_distribution": {k: str(v) for k, v in maturity.items()},
        "exposure_by_period": {k: str(v) for k, v in by_period.items()},
    }
