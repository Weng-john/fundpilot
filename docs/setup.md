# Local setup

Requirements: Python ≥ 3.12, [uv](https://docs.astral.sh/uv/). PostgreSQL 16 optional.
**No exchange account or API key is needed.**

```bash
uv sync                      # create .venv and install deps + dev tools
cp .env.example .env         # optional; defaults work without it
uv run fundpilot db upgrade  # creates ./data/fundpilot.sqlite by default
uv run fundpilot collect --once
uv run fundpilot status
```

### Using PostgreSQL

```bash
docker compose up -d db
export FUNDPILOT_DATABASE_URL=postgresql+psycopg://fundpilot:fundpilot@localhost:5432/fundpilot
uv run fundpilot db upgrade
```

### Running checks

```bash
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy src tests
uv run pytest
```

PostgreSQL integration tests run only when `FUNDPILOT_TEST_DATABASE_URL` is set. It must
point to a **disposable** database: the tests drop and recreate the schema.

```bash
FUNDPILOT_TEST_DATABASE_URL=postgresql+psycopg://fundpilot:fundpilot@localhost:5432/fundpilot_test uv run pytest
```

### CLI reference

| Command | Effect |
|---|---|
| `fundpilot db upgrade` | apply migrations |
| `fundpilot collect [--once] [--iterations N]` | poll ticker, books, stats, trades, BTC/ETH candles |
| `fundpilot stream [--symbol fUST]` | record the public WebSocket stream (reconnects automatically) |
| `fundpilot backfill {stats,trades,candles,volatility} --symbol ... --start ...` | historical pagination (`volatility` takes a trading pair, e.g. `tBTCUSD`) |
| `fundpilot quality [--hours 24]` | staleness + gap checks |
| `fundpilot prune DATASET --older-than-days N [--confirm]` | retention (dry-run by default) |
| `fundpilot demo-data` | write clearly tagged SYNTHETIC data for demos |
| `fundpilot backtest CONFIG [--synthetic-demo]` | strategies × scenarios backtest (SIMULATED) |
| `fundpilot paper run CONFIG` / `paper status --account A` | paper trading |
| `fundpilot emergency-stop --reason R` / `resume --confirm` | operator controls |
| `fundpilot approvals list\|approve ID\|reject ID` | manual approval queue |
| `fundpilot alerts` | recent alerts |
| `fundpilot ml CONFIG [--synthetic-demo]` | rate forecasting / fill probability (walk-forward) |
| `fundpilot rl CONFIG [--synthetic-demo]` | RL training and out-of-sample comparison |
| `fundpilot agent report\|ask\|recommendations` | supervisory agent |
| `fundpilot serve` | dashboard API |
| `fundpilot status` | config summary and row counts |
