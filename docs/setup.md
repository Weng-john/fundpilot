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
| `fundpilot collect [--once] [--iterations N]` | poll ticker, books, stats, trades |
| `fundpilot backfill {stats,trades,candles} --symbol fUST --start 2026-10-01 [--end ...] [--timeframe 1h]` | historical pagination |
| `fundpilot quality [--hours 24]` | staleness + gap checks |
| `fundpilot prune DATASET --older-than-days N [--confirm]` | retention (dry-run by default) |
| `fundpilot status` | config summary and row counts |
