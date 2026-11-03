# Backtesting methodology

All backtest output is **SIMULATED**. It is never realised performance.

## Pipeline

```text
stored observations (origin filter) ─► MarketFeed (point-in-time states)
     ─► FundingSimulator(strategy, scenario, config) ─► snapshots / fills / closes
     ─► compute_metrics ─► ExperimentReport (JSON + Markdown, labelled)
```

## Point-in-time feed (no look-ahead)

`backtesting.feed.MarketFeed.state_at(t)` returns the latest ticker and book observed at or
before `t` and trades in `(t - lookback, t]`. A state is `stale` when either input is older than
`max_staleness` or missing. The simulator treats stale steps as a disruption (no fills, no
decisions). Tests assert that changing data after time *t* cannot change results before *t*.

By default only `origin=exchange_api` rows are loaded. Synthetic rows require
`allow_synthetic = true` / `--allow-synthetic`, and the report is then labelled
**SYNTHETIC DATA — NOT MARKET EVIDENCE**.

## Simulation step order

1. Accrue interest on active credits over `(prev, t]` (per-second, `Decimal`, 40-digit precision);
   FRR credits re-rate to the FRR observed at `prev`. Platform fee (default 15 %) is deducted.
2. Early repayments (Poisson with the scenario's daily hazard, seeded); a minimum of 1 hour of
   interest is charged when repaid earlier. Then maturities release principal.
3. Paid interest moves to available capital every `payout_interval` (default 1 day — an
   **assumption**), so it can be reinvested.
4. Fills from public trades in `(prev, t]` that happened after the offer went live.
5. Strategy decision every `decision_interval`; constraint enforcement; cancels (unfilled
   remainder only) and new offers, live after `latency_steps`. On activation an offer may match
   resting bids immediately (bid crossing).

## Execution scenarios (assumptions, not calibrated)

| Parameter | conservative | neutral | optimistic |
|---|---|---|---|
| Trade must pay **more** than our rate | yes | no (≥) | no (≥) |
| Period match | exact | exact | trade period ≤ ours |
| Queue ahead: share of same-rate depth | 100 % | 50 % | 0 % |
| Participation in eligible volume after queue | 10 % | 30 % | 60 % |
| Share of crossing bid size captured | 25 % | 50 % | 100 % |
| Early repayment hazard / day | 0.5 | 0.25 | 0.1 |
| Latency (steps) | 2 | 1 | 0 |

Queue ahead = resting ask depth at better rates + the share above at the same rate, measured
when the offer goes live. Eligible trade volume first drains the queue, then fills us at the
participation rate. Each public trade's volume is shared across all of our offers (it cannot be
counted twice). Trade amounts are used as absolute values because the sign semantics are
unverified (A5).

Because public data cannot show whether *our* offer would have filled, every conclusion must
hold under the **conservative** scenario to be taken seriously.

## Metrics (`backtesting.metrics`)

Gross / net annualised yield (simple), net yield after operating costs, net APY (compounded,
extrapolated), capital utilisation, idle ratio, unfilled-offer ratio, minimum free-capital ratio,
average remaining lock-up days, max drawdown, fill ratio, median time to first fill, volume-weighted
time to fill, early repayments, stale steps, excess return vs. the configured baseline.

## Simulator integrity tests

`tests/unit/test_simulator_exploits.py` checks, under all scenarios with a random "churn" policy:
capital conservation (equity = initial + net interest at every step), non-negative balances,
fills bounded by public volume × participation, no fills for absurd rates without matching
trades, and that cancelling cannot recall active credits.

## Running experiments

```bash
uv run fundpilot backtest experiments/baseline_comparison.toml            # stored real data
uv run fundpilot backtest experiments/baseline_comparison.toml --synthetic-demo   # pipeline demo
```

Outputs go to `experiments/outputs/<name>/<timestamp>/report.{json,md}` (git-ignored).
Experiments are reproducible from the TOML file, the stored data and the seed.

## Known limitations

* Fill model parameters are guesses until real fill observations exist (paper/live).
* No modelling of hidden offers, the 18 % hidden-offer fee, or exchange FRR re-rating cadence
  (we re-rate every step; Bitfinex reportedly updates FRR loans hourly — unverified).
* Our own offers do not change the book seen by other participants (no market impact).
* Walk-forward evaluation will be added together with learned strategies (Phase 4).
