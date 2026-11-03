# ML / RL experiment guide

**Status: planned (Phases 4–5).**

* Start with statistical baselines (persistence, EWMA, quantiles) before LightGBM/XGBoost.
* Time-series splits / walk-forward only; never random K-fold on time series.
* Fill-probability labels must come from *observed* fills (own paper/live offers). Fills
  inferred from public trades are features or weak labels, never ground truth.
* RL environments reuse the backtester's execution assumptions; tests must detect simulator
  exploitation (e.g. reward from impossible fills). RL policies never execute directly; they
  produce decisions that go through risk and execution layers.
* An AI/ML/RL strategy is "better" only if it beats baselines out-of-sample, net of fees,
  under the conservative execution scenario. A negative result is a valid outcome.
