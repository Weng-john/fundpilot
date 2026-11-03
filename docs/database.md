# Database schema

Managed by Alembic (`migrations/`). Apply with `fundpilot db upgrade`.
PostgreSQL is the target; SQLite is supported for tests and zero-setup local runs.

## Portable types

| Type | PostgreSQL | SQLite |
|---|---|---|
| `ExactDecimal` | `NUMERIC(38,18)` | canonical decimal string (exact) |
| `UTCDateTime` | `TIMESTAMPTZ` | naive UTC, restored as aware UTC |

> SQLite stores decimals as text, so SQL-side numeric ordering/aggregation on those columns is
> not reliable there. Use PostgreSQL for analysis.

## Market data

| Table | Natural key (unique) | Notes |
|---|---|---|
| `funding_tickers` | exchange, symbol, observed_at | `observed_at` = local fetch time (endpoint has no timestamp) |
| `funding_book_snapshots` | exchange, symbol, precision, captured_at | `precision` P0–P4 or R0 |
| `funding_book_levels` | — (FK snapshot, ON DELETE CASCADE) | `side` bid/ask, `amount >= 0` |
| `funding_stats` | exchange, symbol, observed_at | `frr_raw` as reported, `frr` normalised |
| `funding_candles` | exchange, candle_key, open_time | OHLC of rates |
| `funding_trades` | exchange, symbol, trade_id | signed `amount` as reported |

All have `origin` with CHECK ∈ (`exchange_api`, `reconstructed`, `synthetic`).

## Operations

| Table | Purpose |
|---|---|
| `ingestion_runs` | one row per (job, symbol) attempt: status, counts, error |
| `data_quality_issues` | gaps, staleness, implausible values; deduplicated by `fingerprint` |

## Account state (schema only; populated from Phase 3/7)

`funding_wallet_snapshots`, `funding_offer_events`, `funding_credit_events`,
`funding_interest_payments` — each keyed by `account` + `mode` (`paper`/`live`) so paper
results can never be confused with live results.

## Operations (Phase 3, migration `0002`)

| Table | Purpose |
|---|---|
| `control_flags` | persistent switches (`emergency_stop`) with reason, who and when |
| `approval_requests` | manual approval queue: pending / approved / rejected / expired / executed |
| `execution_audit_log` | append-only record of every execution attempt in every mode |
| `execution_idempotency_keys` | duplicate-order prevention |
| `alerts` | risk / operational alerts (deduplicated per code within 30 min) |
| `paper_account_state` | serialised virtual account so paper trading resumes after restarts |

## Retention

`fundpilot prune <dataset> --older-than-days N` reports what *would* be deleted.
Deletion happens only with `--confirm`. Book snapshots dominate storage
(~100 levels × 2 books × symbols × 1/min); the default retention suggestion is 90 days.
