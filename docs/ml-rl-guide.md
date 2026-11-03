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
* External indicators: annualised realised volatility of BTC and ETH over 24 h from **completed**
  1 h trading candles (a candle becomes usable only at its close time), exposed as
  `MarketState.indicators` and the features `btc_rv_24h`, `eth_rv_24h`.
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

Install: `uv sync --extra rl` (gymnasium + Stable-Baselines3, which pulls in PyTorch, several GB).
CI installs only gymnasium; PPO tests are skipped there.

### Environment (`fundpilot.rl.env.FundingEnv`)

* Built on the same `VirtualFundingAccount` as the backtester, with the same execution scenarios,
  exchange constraints and — by default — the same `RiskManager` gate. RL policies therefore
  cannot bypass the risk layer.
* One step = one decision followed by `decision_every` market steps. Episodes start at a random
  point of the training window after `warmup` steps of feature history.
* **Observation** (17 floats, clipped to ±10): FRR level, its deviation/z-score vs. 24 h, 1 h
  change, spread, book imbalance, trade VWAP premium, long-period share, time of day; portfolio
  shares available/offered/lent, average remaining lock-up, lent share by period bucket, unpaid
  interest.
* **Action** (`MultiDiscrete`): rate premium over FRR × period (2/7/30/120 d) × fraction of free
  capital × "cancel unfilled offers first" flag.
* **Reward** (bp of initial capital): net interest accrued − `idle_penalty` × idle capital × FRR ×
  time − `lock_penalty_per_day` × capital locked beyond `max_lock_days` − `concentration_penalty`
  × excess period concentration − `action_cost` × actions. Each component is reported in `info`.

### Algorithms

| Policy | Description |
|---|---|
| `constant_frr_2d_all` | heuristic: everything at FRR, 2 days |
| `cem` | cross-entropy method over a linear policy (numpy, deterministic given seed) |
| `ppo` | Stable-Baselines3 PPO (`MlpPolicy`, CPU) when installed |

### Evaluation (`fundpilot rl CONFIG`)

Policies are trained on the first `train_fraction` of the window (training scenario = first
scenario listed), then wrapped as `PolicyStrategy` and backtested on the unseen remainder with
`FundingSimulator` + risk gate next to the baseline strategies under **every** listed scenario.
Report: `experiments/outputs/rl/<name>/<timestamp>/report.json` with train/test windows, CEM
training curve, test-environment rewards and backtest metrics with excess vs. baseline.

### Simulator-exploitation tests (`tests/unit/test_rl.py`)

* Gymnasium API compliance (`check_env`).
* Reward equals the scaled sum of its components; summed reward interest equals the account's
  accrued net interest (no reward without interest).
* Random actions conserve capital under all scenarios.
* Offers at absurd rates earn nothing (only penalties).
* Observations and rewards before time *t* are unchanged when data after *t* changes.
* Risk limits bind RL actions.
* CEM training is deterministic for a given seed.
