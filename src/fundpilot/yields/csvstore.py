"""Append-only CSV storage for yield observations (used by the scheduled GitHub workflow).

CSV keeps the collector dependency-free (no database in CI) and makes the history easy to
inspect. ``import_dir`` loads the files into the database for the dashboard.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from fundpilot.core.models import DataOrigin
from fundpilot.data.models import CexEarnRateRow
from fundpilot.data.repository import insert_ignore
from fundpilot.defi.aave import markets, store
from fundpilot.defi.aave.models import ReserveSnapshot
from fundpilot.defi.aave.reader import read_snapshot
from fundpilot.defi.rpc import JsonRpcClient, RpcError
from fundpilot.exchanges.okx.earn import EarnRate, OkxEarnClient

logger = logging.getLogger(__name__)

OKX_FIELDS = ["observed_at", "ccy", "borrow_rate", "lending_rate"]
AAVE_FIELDS = [
    "observed_at",
    "chain",
    "symbol",
    "underlying",
    "total_supplied",
    "total_debt",
    "available_liquidity",
    "supply_apr",
    "borrow_apr",
    "reserve_factor",
    "supply_cap",
    "optimal_usage",
    "base_rate",
    "slope1",
    "slope2",
    "is_active",
    "is_frozen",
    "is_paused",
]


def _append(
    path: Path, fields: list[str], rows: Iterable[dict[str, object]], key: tuple[str, ...]
) -> int:
    existing: set[tuple[str, ...]] = set()
    if path.exists():
        with path.open(newline="") as fh:
            existing = {tuple(r[k] for k in key) for r in csv.DictReader(fh)}
    new = [
        r
        for r in ({k: ("" if v is None else str(v)) for k, v in row.items()} for row in rows)
        if tuple(r[k] for k in key) not in existing
    ]
    if not new:
        return 0
    write_header = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerows(sorted(new, key=lambda r: r["observed_at"]))
    return len(new)


def collect_okx(
    directory: Path,
    hours: int = 48,
    ccys: Iterable[str] = ("USDT", "USDC"),
    client: OkxEarnClient | None = None,
) -> dict[str, int]:
    own = client is None
    client = client or OkxEarnClient()
    out = {}
    try:
        start = datetime.now(UTC) - timedelta(hours=hours)
        for ccy in ccys:
            rows = client.backfill(ccy, start)
            out[ccy] = _append(
                directory / f"okx_flexible_{ccy}.csv",
                OKX_FIELDS,
                [
                    {
                        "observed_at": r.observed_at.isoformat(),
                        "ccy": r.ccy,
                        "borrow_rate": r.borrow_rate,
                        "lending_rate": r.lending_rate,
                    }
                    for r in rows
                ],
                ("observed_at",),
            )
    finally:
        if own:
            client.close()
    return out


def collect_aave(directory: Path, rpc_urls: dict[str, str]) -> dict[str, object]:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    snaps: list[ReserveSnapshot] = []
    errors = []
    for chain, url in rpc_urls.items():
        rpc = JsonRpcClient(url)
        try:
            for r in markets.reserves(chains=(chain,)):
                try:
                    snaps.append(read_snapshot(rpc, r, now))
                except (RpcError, ValueError) as exc:
                    errors.append(f"{r.key}: {exc}")
        finally:
            rpc.close()
    n = _append(
        directory / "aave_snapshots.csv",
        AAVE_FIELDS,
        [
            {
                f: (s.observed_at.isoformat() if f == "observed_at" else getattr(s, f))
                for f in AAVE_FIELDS
            }
            for s in snaps
        ],
        ("observed_at", "chain", "symbol"),
    )
    return {"stored": n, "errors": errors}


def _d(v: str) -> Decimal | None:
    return None if v == "" else Decimal(v)


def import_dir(session: Session, directory: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(directory.glob("okx_flexible_*.csv")):
        with path.open(newline="") as fh:
            rows = [
                EarnRate(
                    ccy=r["ccy"],
                    observed_at=datetime.fromisoformat(r["observed_at"]),
                    borrow_rate=_d(r["borrow_rate"]),
                    lending_rate=_d(r["lending_rate"]),
                )
                for r in csv.DictReader(fh)
            ]
        counts[path.name] = insert_ignore(
            session,
            CexEarnRateRow,
            [r.model_dump() | {"origin": DataOrigin.EXCHANGE_API.value} for r in rows],
        )
    aave_path = directory / "aave_snapshots.csv"
    if aave_path.exists():
        with aave_path.open(newline="") as fh:
            snaps = [
                ReserveSnapshot(
                    chain=r["chain"],
                    symbol=r["symbol"],
                    underlying=r["underlying"],
                    observed_at=datetime.fromisoformat(r["observed_at"]),
                    total_supplied=Decimal(r["total_supplied"]),
                    total_debt=Decimal(r["total_debt"]),
                    available_liquidity=Decimal(r["available_liquidity"]),
                    supply_apr=Decimal(r["supply_apr"]),
                    borrow_apr=Decimal(r["borrow_apr"]),
                    reserve_factor=Decimal(r["reserve_factor"]),
                    supply_cap=_d(r["supply_cap"]),
                    optimal_usage=_d(r["optimal_usage"]),
                    base_rate=_d(r["base_rate"]),
                    slope1=_d(r["slope1"]),
                    slope2=_d(r["slope2"]),
                    is_active=r["is_active"] == "True",
                    is_frozen=r["is_frozen"] == "True",
                    is_paused=r["is_paused"] == "True",
                )
                for r in csv.DictReader(fh)
            ]
        counts[aave_path.name] = store.save(session, snaps)
    return counts
