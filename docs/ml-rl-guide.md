# ML / RL experiment guide

Install the optional extra: `uv sync --extra ml` (numpy, LightGBM). The dev group already
includes them.

## Principles

* Statistical baselines first; a learned model is only useful if it beats them
  **out-of-sample**, and a strategy using it only if it beats baseline strategies **net of fees
  under the conservative execution scenario**. A negative result is a valid outcome.
* Time-series validation only: expanding walk-forward splits; training rows whose labels overlap
  the test block are **purged** (`label time = t + horizon < test start - embargo`).
* Features are point-in-time (`ml.features.FeatureBuilder` sees only the current and earlier
  states) and use **time-based** windows (1 h / 6 h / 24 h), so they mean the same thing at any
  sampling cadence (training vs. live decision interval).
* Label provenance is explicit and stored with every model:

| `label_source` | Origin | Ground truth? |
|---|---|---|
| `observed` | offer events of **live** offers | yes |
| `simulated` | offer events of **paper** offers (fill model) | **no** |
| `inferred` | hypothetical offers labelled from public trades + book depth | **no** |

## Task 1 — rate forecasting

Target: `log(FRR(t+h) / FRR(t))`; labels must fall inside the data window. Models:
`persistence` (no change), `mean_reversion` (linear in `log(FRR / 24h mean)`), `lightgbm`
(L2 point forecast + 10 %/90 % quantile models for the prediction interval). Baselines' intervals
come from training-residual quantiles. Metrics on the rate scale: MAE, RMSE, skill vs. persistence,
80 % interval coverage and width.

## Task 2 — fill probability

Probability that an offer (rate premium vs. FRR, period, amount, market features) fills within
the window. Models: `binned_empirical` (Laplace-smoothed frequency by premium bin × period) and
`lightgbm_calibrated` (binary LightGBM + isotonic calibration fitted on the most recent 20 % of the
training window). Metrics: Brier, log loss, AUC, expected calibration error and a reliability table,
plus a constant base-rate reference.

Inferred labels: an offer at `FRR × (1 + premium)` is labelled filled if public volume at ≥ that
rate and the same period within the window exceeds the ask depth ahead of it plus its amount. This
ignores hidden liquidity, cancellations and matching rules, so it is an approximation.

## Running

```bash
uv run fundpilot ml experiments/ml_rate_forecast.toml
uv run fundpilot ml experiments/ml_fill_probability.toml
# pipeline demo on SYNTHETIC data (results are not market evidence):
uv run fundpilot ml experiments/ml_rate_forecast.toml --synthetic-demo
```

Reports: `experiments/outputs/ml/<name>/<timestamp>/report.json`.

## Model registry and versioning

`models/<task>/<version>/` (git-ignored) holds `metadata.json` (training window, label horizon,
feature names, label source, out-of-sample evaluation, gain feature importance, git revision),
`evaluation.json` and the model files (LightGBM text models, isotonic calibrator JSON). The version
is a hash of the configuration and the training-data fingerprint, so identical inputs give the
same version.

## Using a model in a strategy

`FillAwareRateStrategy` (`type = "fill_aware"`, `model_dir = ...` in an experiment TOML) chooses
the premium maximising `rate × P(fill)`, subject to a minimum probability. Its training data ends
at `last sample + label horizon`; the experiment runner raises `LeakageError` if that is not before
the backtest start.

## Reinforcement learning

Planned for Phase 5: a Gymnasium environment over `VirtualFundingAccount` that reuses the same
execution scenarios, with tests for simulator exploitation. RL policies will only emit decisions
that pass the risk manager and execution gateway.
