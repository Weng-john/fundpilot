"""Supervisory agent: deterministic reports, optional LLM commentary and Q&A with read-only tools.

Safety properties (tested):

* the tool registry contains only read-only analytics plus ``propose_recommendation``, which
  stores a *validated proposal* for human review;
* there is no tool that can place, modify or cancel offers, change controls, or approve anything;
* tool output is data; LLM text is never executed; unknown tools or bad arguments are refused;
* the number of LLM/tool rounds is bounded.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.agents import analytics
from fundpilot.agents.llm import LLMError, OpenAICompatibleClient
from fundpilot.agents.recommendations import RULES, RecommendationStore
from fundpilot.core.time import utc_now
from fundpilot.data.db import session_scope
from fundpilot.data.models import AgentActivityRow, AlertRow

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are FundPilot's supervisory analyst for crypto margin-funding (lending).
Rules:
- You can only read data through the provided tools and propose configuration changes with
  propose_recommendation. You cannot place, change or cancel offers and must never claim to.
- Use only numbers returned by tools; say "unknown" when data is missing. Do not invent data.
- Always distinguish REALISED (live), SIMULATED (backtest/paper) and FORECAST figures.
- Active funding credits cannot be recalled before the borrower repays or the term ends.
- Rates are daily fractions; APR = daily x 365. Be concise."""


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    writes: bool = False  # True only for propose_recommendation (stores a proposal)

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


@dataclass(slots=True)
class SupervisorAgent:
    sessions: sessionmaker[Session]
    symbol: str = "fUST"
    llm: OpenAICompatibleClient | None = None
    reports_root: Path = Path("experiments/outputs")
    max_rounds: int = 6
    clock: Callable[[], datetime] = utc_now
    tools: dict[str, Tool] = field(init=False)

    def __post_init__(self) -> None:
        self.tools = {t.name: t for t in self._build_tools()}

    # -- tools -----------------------------------------------------------------
    def _build_tools(self) -> list[Tool]:
        def market(symbol: str | None = None) -> Any:
            with session_scope(self.sessions) as s:
                return analytics.market_summary(s, symbol or self.symbol, self.clock())

        def anomalies(symbol: str | None = None, z_threshold: float = 3.0) -> Any:
            with session_scope(self.sessions) as s:
                return analytics.rate_anomalies(
                    s, symbol or self.symbol, self.clock(), z_threshold=float(z_threshold)
                )[-20:]

        def utilization(account: str, mode: str = "paper", days: int = 7) -> Any:
            if mode not in {"paper", "live"}:
                raise ValueError("mode must be paper or live")
            with session_scope(self.sessions) as s:
                return analytics.utilization_review(s, account, mode, self.clock(), int(days))

        def operations(hours: int = 24) -> Any:
            with session_scope(self.sessions) as s:
                return analytics.operational_review(s, self.clock(), int(hours))

        def alerts(limit: int = 20) -> Any:
            with session_scope(self.sessions) as s:
                rows = s.scalars(
                    select(AlertRow).order_by(AlertRow.at.desc()).limit(min(int(limit), 100))
                )
                return [
                    {"at": r.at, "code": r.code, "severity": r.severity, "message": r.message}
                    for r in rows
                ]

        def experiment(path: str) -> Any:
            return analytics.experiment_summary(Path(path), self.reports_root)

        def propose(parameter: str, value: str, rationale: str, current: str | None = None) -> Any:
            rec_id, check = RecommendationStore(self.sessions).propose(
                parameter,
                value,
                rationale,
                source=f"llm:{self.llm.model if self.llm else '?'}",
                current=current,
                now=self.clock(),
            )
            return {
                "stored": rec_id is not None,
                "id": rec_id,
                "errors": list(check.errors),
                "risk_increasing": check.risk_increasing,
                "note": "Stored for human review; nothing is applied automatically.",
            }

        sym = {"symbol": {"type": "string", "description": "funding symbol, e.g. fUST"}}
        return [
            Tool(
                "market_summary",
                "Current FRR, 24h range, 7d percentile, book and trades.",
                _obj(sym, []),
                market,
            ),
            Tool(
                "rate_anomalies",
                "FRR observations far from the trailing 24h mean.",
                _obj({**sym, "z_threshold": {"type": "number"}}, []),
                anomalies,
            ),
            Tool(
                "utilization_review",
                "Idle share and equity for a paper or live account.",
                _obj(
                    {
                        "account": {"type": "string"},
                        "mode": {"type": "string", "enum": ["paper", "live"]},
                        "days": {"type": "integer"},
                    },
                    ["account"],
                ),
                utilization,
            ),
            Tool(
                "operational_review",
                "Ingestion failures, data-quality issues and alerts.",
                _obj({"hours": {"type": "integer"}}, []),
                operations,
            ),
            Tool(
                "recent_alerts",
                "Most recent risk/operational alerts.",
                _obj({"limit": {"type": "integer"}}, []),
                alerts,
            ),
            Tool(
                "experiment_summary",
                "Summarise a backtest/ML/RL report JSON under experiments/outputs.",
                _obj({"path": {"type": "string"}}, ["path"]),
                experiment,
            ),
            Tool(
                "propose_recommendation",
                "Propose a configuration change for human review. "
                f"Allowed parameters: {', '.join(sorted(RULES))}.",
                _obj(
                    {
                        "parameter": {"type": "string", "enum": sorted(RULES)},
                        "value": {"type": "string"},
                        "rationale": {"type": "string"},
                        "current": {"type": "string"},
                    },
                    ["parameter", "value", "rationale"],
                ),
                propose,
                writes=True,
            ),
        ]

    def call_tool(self, name: str, arguments: str | dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
            allowed = set(tool.parameters["properties"])
            extra = set(args) - allowed
            if extra:
                return {"error": f"unexpected arguments: {sorted(extra)}"}
            result = tool.fn(**args)
        except Exception as exc:  # tool errors are returned to the model, never raised
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"result": analytics.jsonable(result)}

    # -- activity log ------------------------------------------------------------
    def _log(self, kind: str, summary: str, detail: dict[str, Any]) -> None:
        with session_scope(self.sessions) as s:
            s.add(
                AgentActivityRow(
                    at=self.clock(),
                    kind=kind,
                    summary=summary[:2000],
                    detail=analytics.jsonable(detail),
                )
            )

    # -- reports -----------------------------------------------------------------
    def facts(self, period: str, account: str | None = None) -> dict[str, Any]:
        hours = 24 if period == "daily" else 24 * 7
        facts: dict[str, Any] = {
            "period": period,
            "generated_at": self.clock(),
            "market": self.call_tool("market_summary", {}),
            "anomalies": self.call_tool("rate_anomalies", {}),
            "operations": self.call_tool("operational_review", {"hours": hours}),
        }
        if account:
            facts["paper_account"] = self.call_tool(
                "utilization_review",
                {"account": account, "mode": "paper", "days": 1 if period == "daily" else 7},
            )
        return analytics.jsonable(facts)  # type: ignore[no-any-return]

    def report(
        self, period: str = "daily", *, account: str | None = None, use_llm: bool = False
    ) -> str:
        if period not in {"daily", "weekly"}:
            raise ValueError("period must be daily or weekly")
        facts = self.facts(period, account)
        text = render_report(facts)
        if use_llm:
            if self.llm is None:
                text += "\n_AI commentary unavailable: no LLM configured._\n"
            else:
                try:
                    msg = self.llm.chat(
                        [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": "Write a short commentary (<= 200 words) on "
                                "these facts. Do not add numbers that are not present.\n"
                                + json.dumps(facts),
                            },
                        ]
                    )
                    text += (
                        "\n## AI commentary (generated by "
                        f"`{self.llm.model}`; verify against the facts above)\n\n"
                        f"{msg.get('content') or ''}\n"
                    )
                except LLMError as exc:
                    text += f"\n_AI commentary failed: {exc}_\n"
        self._log("report", f"{period} report", {"facts": facts, "llm": use_llm})
        return text

    # -- Q&A -----------------------------------------------------------------------
    def ask(self, question: str) -> dict[str, Any]:
        if self.llm is None:
            raise LLMError("no LLM configured (set FUNDPILOT_LLM_BASE_URL and _MODEL)")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        specs = [t.spec() for t in self.tools.values()]
        calls: list[dict[str, Any]] = []
        for _ in range(self.max_rounds):
            msg = self.llm.chat(messages, specs)
            tool_calls = msg.get("tool_calls") or []
            messages.append(
                {k: v for k, v in msg.items() if k in {"role", "content", "tool_calls"}}
            )
            if not tool_calls:
                answer = str(msg.get("content") or "")
                self._log("question", question, {"answer": answer, "tool_calls": calls})
                return {"answer": answer, "tool_calls": calls}
            for call in tool_calls:
                fn = call.get("function", {})
                result = self.call_tool(str(fn.get("name")), fn.get("arguments") or "{}")
                calls.append(
                    {
                        "name": fn.get("name"),
                        "arguments": fn.get("arguments"),
                        "ok": "error" not in result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(result)[:20_000],
                    }
                )
        self._log(
            "question", question, {"answer": None, "tool_calls": calls, "error": "max rounds"}
        )
        return {
            "answer": "Stopped: too many tool rounds without a final answer.",
            "tool_calls": calls,
        }


def _fmt(x: Any, pct: bool = False, digits: int = 6) -> str:
    if x is None:
        return "unknown"
    if pct:
        return f"{float(x) * 100:.2f}%"
    if isinstance(x, float):
        return f"{x:.{digits}g}"
    return str(x)


def render_report(facts: dict[str, Any]) -> str:
    """Deterministic Markdown report (no LLM)."""
    m = facts["market"].get("result", {})
    ops = facts["operations"].get("result", {})
    an = facts["anomalies"].get("result", [])
    lines = [
        f"# FundPilot {facts['period']} report",
        "",
        f"Generated {facts['generated_at']} (UTC). Market figures are recorded exchange "
        "observations; account figures are labelled REALISED or SIMULATED.",
        "",
        f"## Market ({m.get('symbol', '?')})",
        "",
        f"- FRR (daily): {_fmt(m.get('frr_daily'))} → APR {_fmt(m.get('frr_apr'), True)} gross, "
        f"{_fmt(m.get('frr_net_apr_after_fee'), True)} after the assumed lender fee",
        f"- 24h FRR range: {_fmt(m.get('frr_24h_min'))} - {_fmt(m.get('frr_24h_max'))} "
        f"(mean {_fmt(m.get('frr_24h_mean'))}); 7-day percentile "
        f"{_fmt(m.get('frr_7d_percentile'), True)}",
        f"- Book: best bid {_fmt(m.get('best_bid'))}, best ask {_fmt(m.get('best_ask'))}, "
        f"ask depth {_fmt(m.get('ask_depth'))}, bid depth {_fmt(m.get('bid_depth'))}",
        f"- Trades 24h: {m.get('trades_24h', 0)} (volume {_fmt(m.get('volume_24h'))}, VWAP "
        f"{_fmt(m.get('vwap_24h'))})",
        f"- Last ticker: {m.get('last_ticker_at') or 'none'}",
        "",
        "## Unusual rates",
        "",
    ]
    if an:
        lines += [
            f"- {a['at']}: FRR {_fmt(a['frr'])} ({a['kind']}, z={a['zscore']:.1f})"
            for a in an[-10:]
        ]
    else:
        lines.append("- none detected (z ≥ 3 vs trailing 24h)")
    lines += [
        "",
        "## Operations",
        "",
        f"- Ingestion runs: {ops.get('ingestion_runs', 0)}, failures: "
        f"{ops.get('ingestion_failures', 0)} {ops.get('failures_by_job') or ''}",
        f"- Data-quality issues: {ops.get('quality_issues') or 'none'}",
        f"- Alerts: {ops.get('alerts') or 'none'}",
    ]
    for msg in ops.get("critical_alerts") or []:
        lines.append(f"  - CRITICAL: {msg}")
    if "paper_account" in facts:
        u = facts["paper_account"].get("result", {})
        lines += [
            "",
            f"## Account `{u.get('account')}` — {u.get('label')}",
            "",
            f"- Snapshots: {u.get('snapshots')}, equity {_fmt(u.get('first_equity'))} → "
            f"{_fmt(u.get('last_equity'))}",
            f"- Idle share: avg {_fmt(u.get('avg_idle_share'), True)}, max "
            f"{_fmt(u.get('max_idle_share'), True)}",
        ]
    lines += ["", "_Active funding credits cannot be recalled before repayment or maturity._", ""]
    return "\n".join(lines)


def recent_activity(
    sessions: sessionmaker[Session], since: timedelta = timedelta(days=7)
) -> list[AgentActivityRow]:
    with session_scope(sessions) as s:
        return list(
            s.scalars(
                select(AgentActivityRow)
                .where(AgentActivityRow.at > utc_now() - since)
                .order_by(AgentActivityRow.at.desc())
            )
        )
