# FundPilot

An open, modular framework for researching and (eventually) operating crypto
margin-funding / stablecoin lending strategies. First target: **Bitfinex USDt margin funding**.

The central research question:

> Can an AI-assisted funding optimisation system consistently outperform simple funding
> strategies after fees, execution uncertainty, capital utilisation, liquidity constraints
> and operational risk?

FundPilot does **not** assume the answer is yes.

> **Picking up this project?** Read [HANDOFF.md](HANDOFF.md) first.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 Foundation | tooling, CI, settings, Bitfinex public adapter, DB schema, collection, backfill, data quality, retention | ✅ implemented, tested with mocked responses |
| 2 Baselines | strategy interface, 5 baseline strategies, event-driven backtester with 3 execution scenarios, metrics, reproducible experiments | ✅ implemented; **no real-data results yet** |
| 3 Paper trading | virtual execution on live data, independent risk manager, execution gateway (dry-run/paper; live refused), idempotency, audit log, manual approvals, emergency stop, alerts | ✅ implemented, tested with scripted data |
| 4 Machine learning | point-in-time features, rate forecasting and fill-probability models (baselines + LightGBM), walk-forward evaluation, calibration, registry, leakage guard | ✅ implemented; no real-data evaluation yet |
| 5 Reinforcement learning | Gymnasium env on the shared account model with the risk gate, CEM + PPO, out-of-sample comparison with baselines, exploitation tests | ✅ implemented; no real-data evaluation yet |
| 6 AI agent | deterministic daily/weekly reports, anomaly detection, read-only tool-calling Q&A via any OpenAI-compatible API, validated human-reviewed recommendations | ✅ implemented; works without an LLM |
| Dashboard | FastAPI read-only API + React/TypeScript/Vite UI: overview, market analytics, strategy/ML/RL results, risk monitoring, agent | ✅ implemented |
| Aave V3 USDT/USDC | read-only reserve reader (JSON-RPC), verified rate model, impact- and cost-aware allocator, backtester vs static/APY-chasing baselines, `fundpilot aave` CLI | ✅ implemented; no live Aave data yet (RPC hosts not reachable from dev env) |
| 7 Live funding | authenticated adapter, live execution | **not started — requires explicit human approval** |

**Important limitations**

* The Bitfinex adapter has **not yet been run against the live API**, and several unit/format
  assumptions (e.g. the `fUST` symbol, funding-stats FRR scale) are unverified. See
  [docs/bitfinex-integration.md](docs/bitfinex-integration.md).
* There is no live trading code. Nothing in this repository can place, modify or cancel real
  exchange orders; `live` execution mode is refused by design.
* No market data or performance results are included or claimed.

## Quick start (no API keys needed)

```bash
uv sync
uv run fundpilot db upgrade        # SQLite at ./data/fundpilot.sqlite by default
uv run fundpilot collect --once    # public Bitfinex funding data for fUST
uv run fundpilot status
uv run pytest                      # all tests use mocked exchange responses
# exercise the backtester on clearly labelled SYNTHETIC data (not market evidence):
uv run fundpilot backtest experiments/baseline_comparison.toml --synthetic-demo
```

PostgreSQL via Docker:

```bash
docker compose up -d --build       # db + migrate + collector
```

More: [setup](docs/setup.md) · [architecture](docs/architecture.md) ·
[database](docs/database.md) · [Bitfinex integration](docs/bitfinex-integration.md) ·
[deployment](docs/deployment.md) · [security](docs/security.md) ·
[risk management](docs/risk-management.md) · [paper trading](docs/paper-trading.md) · [AI agent](docs/agent.md) · [dashboard](docs/dashboard.md) · [Aave](docs/aave.md) · [yield comparison](docs/yields.md) · [strategy guide](docs/strategy-development.md) ·
[backtesting](docs/backtesting.md) · [ML/RL guide](docs/ml-rl-guide.md)

## Design principles

* All rates are **daily decimal fractions** internally; APR/APY are derived, and APY is labelled
  a hypothetical upper bound.
* `Decimal` for every financial value (JSON is parsed with `parse_float=Decimal`); aware UTC
  timestamps only.
* Every stored market row carries `origin` (`exchange_api` / `reconstructed` / `synthetic`).
* Idempotent ingestion; per-job failure isolation recorded in `ingestion_runs`.
* Exchange-specific logic stays inside `fundpilot.exchanges.<exchange>`.
* Safe defaults: `dry_run`, no credential settings, destructive operations require `--confirm`.

## License

MIT — see [LICENSE](LICENSE). Nothing here is investment advice.
