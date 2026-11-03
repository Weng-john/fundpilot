# Roadmap

Each phase maps to a GitHub milestone. A phase is complete only when its code is tested,
documented, and CI is green.

| Phase | Milestone | Status |
|---|---|---|
| 1 | Foundation: tooling, CI, settings, Bitfinex public adapter, schema, collection pipeline | **Implemented** (API assumptions pending verification) |
| 2 | Quantitative baselines: strategy interface, 5 baselines, event-driven backtester, metrics | **Implemented** (no real-data results yet: no stored exchange data) |
| 3 | Paper trading: virtual execution, portfolio tracking, risk manager, alerts | **Implemented** (not yet run against live data: Bitfinex unreachable from dev env) |
| 4 | Machine learning: features, rate forecasting, fill probability, calibration | **Implemented** (evaluated only on synthetic demo data so far) |
| 5 | Reinforcement learning: Gymnasium env, PPO vs baselines | **Implemented** (evaluated only on synthetic demo data so far) |
| 6 | AI agent: summaries, explanations, reports (no execution access) | Not started |
| 7 | Live funding: authenticated adapter, execution gateway, approvals, security review | Not started — requires explicit human approval |

## Phase 1 follow-ups

* Verify assumptions A1–A15 in `docs/bitfinex-integration.md` against official docs / live API.
* WebSocket subscriptions with reconnection (public funding book/trades).
* BTC/ETH volatility indicators (from trading candles) for the feature set.
* Minimal FastAPI read-only API (`apps/api`) for the dashboard.
