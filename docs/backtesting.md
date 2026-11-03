# Backtesting methodology

**Status: planned for Phase 2.**

Principles fixed now so Phase 1 data supports them:

* Event-driven replay over stored observations only (`origin = exchange_api` by default).
  Reconstructed/synthetic inputs must be opted into and labelled in every report.
* No look-ahead: a decision at time *t* sees only rows with timestamp ≤ *t*.
* Fills cannot be observed from public data. Fill models must be explicit assumptions with
  **conservative / neutral / optimistic** scenarios; results are reported per scenario.
* Fees (15 % / 18 % hidden, per support docs via search), minimum offer size, period limits,
  early repayment and capital lock-up are modelled explicitly.
* Results are **simulated** and must never be presented as realised returns.
