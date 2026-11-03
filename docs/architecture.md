# Architecture

## Layout decision

The proposed `packages/*` monorepo was simplified to **one Python distribution**
(`src/fundpilot`) with sub-packages. Reasons: one `pyproject.toml`, one lockfile,
one mypy/ruff config, no inter-package version skew. Sub-packages keep the same
boundaries and can be split later if a real need appears. `apps/web` (React) will
be added in the dashboard phase.

```text
src/fundpilot/
├── config.py            # pydantic-settings, FUNDPILOT_* env vars
├── cli.py               # `fundpilot` command
├── core/                # exchange-agnostic: rates, money, time, normalised models
├── exchanges/
│   ├── base.py          # FundingMarketDataAdapter protocol
│   ├── ratelimit.py     # token bucket
│   ├── errors.py
│   └── bitfinex/        # ALL Bitfinex-specific code: client, parsers, symbols, adapter
├── strategies/          # Strategy protocol, constraints, 5 baselines (no exchange access)
├── backtesting/         # point-in-time feed, simulator, scenarios, metrics, experiments, synthetic data
├── risk/                # RiskManager, limits, health blockers
├── execution/           # ExecutionGateway (dry_run/paper; live refused), controls, approvals
├── ml/                  # features, datasets (label provenance), models, walk-forward, registry
├── paper/               # live market source, health monitor, paper trading engine
└── data/
    ├── types.py         # ExactDecimal / UTCDateTime portable column types
    ├── models.py        # SQLAlchemy schema
    ├── repository.py    # idempotent inserts + queries
    ├── ingestion.py     # periodic collector, per-job isolation, ingestion_runs
    ├── backfill.py      # backward pagination
    ├── quality.py       # gaps, staleness, plausibility -> data_quality_issues
    ├── retention.py     # dry-run-by-default pruning
    └── migrate.py       # programmatic Alembic
migrations/              # Alembic revisions
```

Planned (not yet present): `optimization/`, `rl/`,
`agents/`, `apps/api`, `apps/web`.

## Data flow (Phase 1)

```text
Bitfinex REST ──► BitfinexRestClient (rate limit, retry, error map, Decimal JSON)
                    │
                    ▼
               parsers.py ──► core.models (normalised, UTC, Decimal, origin)
                    │
                    ▼
     MarketDataCollector / backfill ──► repository (INSERT .. ON CONFLICT DO NOTHING)
                    │                           │
                    ▼                           ▼
           quality checks ──► data_quality_issues   ingestion_runs
```

## Key invariants

1. **Rates** are daily decimal fractions everywhere outside an adapter.
2. **Money** is `Decimal`; floats are rejected by `to_decimal` and `ExactDecimal`.
3. **Time** is aware UTC; naive datetimes raise.
4. **Provenance**: every market row has `origin` ∈ {exchange_api, reconstructed, synthetic}.
5. **Idempotency**: unique keys on natural identifiers; re-running ingestion is safe.
6. **Isolation**: strategies/backtests will depend only on `core` models and adapter protocols.
7. **Safety**: no code path in Phase 1 can place, change or cancel an order; there are no credential settings.

## Future separation of simulation and live

Execution will go through a single gateway (`execution/`) with `dry_run` (default),
`paper`, and `live` modes. Strategies, ML/RL models and the AI agent will only emit
*decisions*; the independent risk manager validates them before the gateway acts,
and live mode will require explicit configuration plus human approval.
