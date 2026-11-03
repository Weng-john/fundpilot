# Paper trading and execution

Paper trading runs a strategy against **live public market data** with **simulated fills**.
No credentials are used and nothing is sent to the exchange. Results are stored with
`mode='paper'` and are simulated, never live performance.

## Flow per poll

```text
LiveMarketSource (ticker, book, trades; future-dated trades ignored)
  └─► HealthMonitor (API failures, staleness, platform status, stablecoin price, emergency flag)
  └─► VirtualFundingAccount.advance  (accrual, repayments, maturities, payouts, fills)
  └─► Strategy.decide  (skipped when data is stale)
  └─► ExecutionGateway.execute
        ├─ RiskManager.evaluate  (limits, blockers, approvals)
        ├─ approved requests from the manual queue (re-checked by risk)
        ├─ idempotency key per (account, decision id, action, index, payload)
        ├─ backend: dry_run → nothing | paper → virtual account | live → refused
        └─ execution_audit_log row for every attempt (simulated/submitted/duplicate/failed)
  └─► persist: offer/credit events, payouts, wallet snapshot, account state (resume after restart)
```

The fill model is the same `VirtualFundingAccount` used by the backtester, under a chosen
execution scenario (`conservative` recommended).

## Running

```bash
uv run fundpilot db upgrade
uv run fundpilot paper run experiments/paper_trading.toml          # loops every poll interval
uv run fundpilot paper status --account paper-ladder
```

Configuration: `experiments/paper_trading.toml` (account, symbol, scenario, strategy, capital,
risk limits, depeg pair).

## Execution modes

| Mode | Backend | Status |
|---|---|---|
| `dry_run` (default) | none; audit rows marked `simulated` | available |
| `paper` | virtual account | available |
| `live` | — | **refused** (`LiveExecutionDisabledError`). Implementing it is Phase 7 and requires explicit human approval. |

## Guarantees and limits

* Duplicate prevention: an idempotency key already recorded is never executed again.
* Transient backend errors are retried with exponential backoff; other errors are recorded as
  `failed` and do not stop the remaining actions.
* State persistence: interrupting and resuming the account yields exactly the same results as
  an uninterrupted run (tested).
* The decision id is `"<account>:<timestamp>"`; idempotency therefore protects against re-submitting
  the same decision, not against a crash between execution and state save (acceptable for paper;
  live mode will need a persisted intent log).
* Paper fills are still model assumptions; they are **not** real fill observations and must not be
  used as fill-probability labels.
