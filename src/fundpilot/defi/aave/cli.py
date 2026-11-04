"""``fundpilot aave ...`` commands (read-only; dry-run recommendations only)."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tomllib
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fundpilot.core.models import DataOrigin
from fundpilot.core.time import ensure_utc, utc_now
from fundpilot.data.db import session_scope
from fundpilot.defi.aave import markets, store
from fundpilot.defi.aave.backtest import (
    AllocatorPolicy,
    BacktestConfig,
    BacktestResult,
    ChaserPolicy,
    Policy,
    StaticPolicy,
    SyntheticMarket,
    simulate,
    synthetic_series,
)
from fundpilot.defi.aave.math import RateModel, apr_to_apy
from fundpilot.defi.aave.reader import read_snapshot
from fundpilot.defi.aave.strategy import AllocatorConfig, plan
from fundpilot.defi.rpc import JsonRpcClient, RpcError

D = Decimal


def add_parser(sub: Any, sessions_factory: Any) -> None:
    aave = sub.add_parser("aave", help="Aave V3 USDT/USDC supply strategy (read-only)")
    a_sub = aave.add_subparsers(dest="aave_command", required=True)
    a_sub.add_parser("markets", help="list configured reserves").set_defaults(
        func=lambda s, a: cmd_markets()
    )
    snap = a_sub.add_parser("snapshot", help="read reserves via JSON-RPC and store them")
    snap.add_argument("--chains", default=None, help="comma list; default = all with RPC URL")
    snap.set_defaults(func=lambda s, a: cmd_snapshot(s, a, sessions_factory))
    pl = a_sub.add_parser("plan", help="dry-run allocation for an amount (latest snapshots)")
    pl.add_argument("--symbol", choices=["USDT", "USDC"], required=True)
    pl.add_argument("--amount", type=Decimal, required=True, help="idle amount to place")
    pl.add_argument("--chain", default="ethereum", help="where the idle amount sits")
    pl.add_argument(
        "--position",
        action="append",
        default=[],
        help="existing position chain=amount (repeatable)",
    )
    pl.add_argument("--horizon-days", type=Decimal, default=D(30))
    pl.set_defaults(func=lambda s, a: cmd_plan(s, a, sessions_factory))
    bt = a_sub.add_parser("backtest", help="backtest supply policies (SIMULATED)")
    bt.add_argument("config")
    bt.add_argument("--synthetic-demo", action="store_true")
    bt.add_argument("--output", default=None)
    bt.set_defaults(func=lambda s, a: cmd_backtest(s, a, sessions_factory))


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_markets() -> int:
    _print(
        [
            {
                "chain": r.market.chain,
                "chain_id": r.market.chain_id,
                "symbol": r.symbol,
                "address_book_symbol": r.book_symbol,
                "underlying": r.underlying,
                "a_token": r.a_token,
                "pool": r.market.pool,
                "data_provider": r.market.data_provider,
            }
            for r in markets.RESERVES
        ]
    )
    return 0


def cmd_snapshot(settings: Any, args: argparse.Namespace, sessions_factory: Any) -> int:
    urls: dict[str, str] = settings.aave_rpc_urls
    chains = args.chains.split(",") if args.chains else list(urls)
    missing = [c for c in chains if c not in urls]
    if missing or not chains:
        print(
            f"no RPC URL for: {missing or 'any chain'}; set FUNDPILOT_AAVE_RPC_URLS="
            '\'{"ethereum": "https://..."}\'',
            file=sys.stderr,
        )
        return 2
    now = utc_now()
    snaps, errors = [], []
    for chain in chains:
        rpc = JsonRpcClient(urls[chain])
        try:
            for r in markets.reserves(chains=(chain,)):
                try:
                    snaps.append(read_snapshot(rpc, r, now))
                except RpcError as exc:
                    errors.append(f"{r.key}: {exc}")
        finally:
            rpc.close()
    with sessions_factory(settings) as sessions, session_scope(sessions) as s:
        stored = store.save(s, snaps)
    _print(
        {
            "stored": stored,
            "errors": errors,
            "reserves": [
                {
                    "key": x.key,
                    "supply_apr": str(x.supply_apr),
                    "supply_apy_est": f"{apr_to_apy(x.supply_apr):.6f}",
                    "utilization": f"{x.utilization:.4f}",
                    "available": str(x.available_liquidity),
                    "cap_headroom": str(x.cap_headroom()),
                }
                for x in snaps
            ],
        }
    )
    return 0 if not errors else 1


def cmd_plan(settings: Any, args: argparse.Namespace, sessions_factory: Any) -> int:
    positions = {}
    for item in args.position:
        chain, amount = item.split("=", 1)
        positions[f"{chain}:{args.symbol}"] = D(amount)
    with sessions_factory(settings) as sessions, session_scope(sessions) as s:
        snaps = store.latest(s, (args.symbol,), max_age=timedelta(hours=6), now=utc_now())
    if not snaps:
        print("no recent snapshots (<6h); run `fundpilot aave snapshot` first", file=sys.stderr)
        return 1
    p = plan(
        args.symbol,
        snaps,
        positions,
        AllocatorConfig(horizon_days=args.horizon_days),
        wallet=(args.chain, args.amount),
        snapshots_include_positions=False,
    )
    _print(
        {
            "mode": "DRY-RUN recommendation (no transactions are sent)",
            "adopted": p.adopted,
            "current_apr": str(p.current_apr),
            "target_apr": str(p.target_apr),
            "expected_gain_over_horizon": str(p.expected_gain),
            "cost": str(p.cost),
            "target": {k: str(v) for k, v in p.target.items() if v > 0},
            "moves": [dataclasses.asdict(m) for m in p.moves],
            "notes": list(p.notes),
            "snapshot_times": {k: v.observed_at for k, v in snaps.items()},
        }
    )
    return 0


def _policies(raw: dict[str, Any], symbol: str, alloc_cfg: AllocatorConfig) -> list[Policy]:
    out: list[Policy] = []
    for p in raw.get("policies", [{"type": "allocator"}]):
        kind = p["type"]
        if kind == "static":
            out.append(StaticPolicy(f"{p['chain']}:{symbol}"))
        elif kind == "chaser":
            out.append(ChaserPolicy(symbol))
        elif kind == "allocator":
            out.append(AllocatorPolicy(symbol, alloc_cfg))
        else:
            raise ValueError(f"unknown policy {kind!r}")
    return out


def _report(results: list[BacktestResult], label: str, raw: dict[str, Any]) -> dict[str, Any]:
    warning = (
        "SYNTHETIC DATA - NOT MARKET EVIDENCE."
        if label == "SYNTHETIC"
        else "SIMULATED on recorded Aave reserve snapshots; not realised returns. Smart-contract,"
        " depeg, bridge and oracle risks are not modelled."
    )
    return {
        "kind": "simulated",
        "data_label": label,
        "warning": warning,
        "experiment": raw.get("name"),
        "results": [
            {
                "policy": r.policy,
                "symbol": r.symbol,
                "start": r.start,
                "end": r.end,
                "days": round(r.days, 3),
                "initial": str(r.initial),
                "final": str(r.final),
                "interest": str(r.interest),
                "costs": str(r.costs),
                "moves": r.moves,
                "blocked_withdrawals": r.blocked_withdrawals,
                "net_apr": r.net_apr,
                "net_apy": r.net_apy,
            }
            for r in results
        ],
    }


def cmd_backtest(settings: Any, args: argparse.Namespace, sessions_factory: Any) -> int:
    raw = tomllib.loads(Path(args.config).read_text())
    symbol = raw["symbol"]
    costs = raw.get("costs", {})
    alloc = AllocatorConfig(
        horizon_days=D(str(raw.get("horizon_days", 30))),
        chunk=D(str(raw.get("chunk", 1000))),
        max_utilization=D(str(raw.get("max_utilization", "0.92"))),
        tx_cost={k: D(str(v)) for k, v in costs.get("tx", {}).items()} or AllocatorConfig().tx_cost,
        bridge_cost=D(str(costs.get("bridge", 5))),
        min_gain=D(str(raw.get("min_gain", 1))),
        spread_half_life_days=(
            None
            if raw.get("spread_half_life_days") == 0
            else D(str(raw.get("spread_half_life_days", 3)))
        ),
    )
    start = ensure_utc(datetime.fromisoformat(raw["start"]))
    end = ensure_utc(datetime.fromisoformat(raw["end"]))
    step = timedelta(seconds=int(raw.get("step_seconds", 3600)))
    if args.synthetic_demo:
        model = RateModel.from_bps(9000, 0, 550, 3500)
        mk = [
            SyntheticMarket(m["chain"], D(str(m["supplied"])), float(m["mean_usage"]), model)
            for m in raw.get(
                "synthetic_markets",
                [
                    {"chain": "ethereum", "supplied": 3e9, "mean_usage": 0.82},
                    {"chain": "arbitrum", "supplied": 4e8, "mean_usage": 0.86},
                    {"chain": "base", "supplied": 1.5e8, "mean_usage": 0.88},
                ],
            )
        ]
        steps = int((end - start) / step)
        data = synthetic_series(
            mk, symbol, start=start, steps=steps, step=step, seed=int(raw.get("seed", 0))
        )
        label = "SYNTHETIC"
    else:
        with sessions_factory(settings) as sessions, session_scope(sessions) as s:
            data = store.series(s, symbol, start, end, step, (DataOrigin.EXCHANGE_API,))
        label = "exchange_api"
        if len(data) < 2:
            print(
                "not enough stored snapshots in the window; collect with "
                "`fundpilot aave snapshot` (e.g. hourly)",
                file=sys.stderr,
            )
            return 1
    bt = BacktestConfig(
        symbol,
        D(str(raw["capital"])),
        raw.get("start_chain", "ethereum"),
        int(raw.get("rebalance_every", 24)),
        alloc,
        raw.get("withdrawal_liquidity", "observed"),
    )
    results = [simulate(data, p, bt) for p in _policies(raw, symbol, alloc)]
    report = _report(results, label, raw)
    out = (
        Path(args.output)
        if args.output
        else (
            Path("experiments/outputs/aave")
            / str(raw.get("name", "aave"))
            / utc_now().strftime("%Y%m%dT%H%M%SZ")
        )
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"> {report['warning']}")
    for r in report["results"]:
        print(
            f"{r['policy']:28} net APR {r['net_apr'] * 100:7.3f}%  costs {float(r['costs']):9.2f}"
            f"  moves {r['moves']:4}  blocked {r['blocked_withdrawals']}"
        )
    print(f"report: {out / 'report.json'}", file=sys.stderr)
    return 0
