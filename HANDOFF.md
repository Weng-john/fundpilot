# Handoff — current status and next steps

Last updated: 2026-11-03 (UTC). Keep this file current: any agent picking up the work starts here.

## Where things stand

- `main` contains Phases 1–6 plus the dashboard. These were merged in PR Weng-john/fundpilot#3.
- Branch `claude/friendly-cerf-nsdxqv` holds follow-up work on top of `main`. No PR is open for it yet. Its commits:
  1. `ea94b30` fix(exchanges): per-endpoint rate limits from the docs, plus detection of the `{"error":"ERR_RATE_LIMIT"}` body.
  2. Same commit, WebSocket: re-subscribe after maintenance code 20061, handle the platform status flag, wait at least 3 s before reconnecting.
  3. `3a3d609` fix: only funding *taken* (trade amount < 0) fills resting offers, except in the optimistic scenario.
  4. Same commit: the order-book-aware strategy only compares bids/asks with matching periods.
  5. `fca8418` feat: `reconstruct-tickers`, book-less backtests (`require_book = false`), backfill page size 10000, `experiments/real_baselines_30d.toml`, and the verification table in `docs/bitfinex-integration.md`.
- Local checks on that branch pass: ruff, format, mypy strict, 231 tests (+6 PostgreSQL tests, skipped without a DB URL). CI has not run on it yet.

## Verified facts (2026-11-03)

Sources: docs.bitfinex.com fetched as `.md` with `curl`, plus live API calls. Full table: `docs/bitfinex-integration.md`.

- **A1–A8, A10, A11, A16 and the WebSocket items are verified.** A1: `fUST` is Tether USDt. A2/A3: rates are daily fractions, and funding stats FRR × 365 = daily rate.
- **A12–A15 have no verified source yet:** 2–120 days, minimum offer 150, 15 % fee, per-second interest. `support.bitfinex.com` is not on the network allow-list.

## Environment notes (cloud session)

- Allowed hosts: `api-pub.bitfinex.com` and `docs.bitfinex.com`.
  - Use `curl` for both. The WebFetch tool cannot resolve docs.bitfinex.com.
  - Append `.md` to doc URLs to get Markdown.
- PostgreSQL 16 runs locally. If it is down: `service postgresql start`.
  - Dev DB: `postgresql+psycopg://fundpilot:fundpilot@localhost:5432/fundpilot_dev`, migrated to head.
  - Test DB: `.../fundpilot_test`.
  - **The local DB lives only in this container.** It is lost when the container is reclaimed; re-run the backfill to rebuild it.
- Run integration tests with:
  `FUNDPILOT_TEST_DATABASE_URL=postgresql+psycopg://fundpilot:fundpilot@localhost:5432/fundpilot_test uv run pytest`
- `uv sync --all-extras` installs ML/RL/API extras. The rl extra pulls PyTorch, several GB.
- Never use `pkill -f <pattern>` / `pgrep -f`. It matches the shell running it and kills the command. Use `ps -eo pid,args | grep "[p]attern"` instead.

## Real data collected so far (in `fundpilot_dev` only, not committed)

- `collect --once` succeeded for every job: ticker, P0/R0 book, stats, trades, BTC/ETH candles.
- Backfill from 2026-10-04 onward:
  - funding stats: 719 rows (hourly)
  - fUST 1h candles `a30:p2:p30`: 729 rows
  - tBTCUSD / tETHUSD 1h candles: 726 rows each
- `fUST` trades: **496,471 rows, 2026-10-04 00:01 → 2026-11-03 08:24 (complete, no warnings)**.
  - Roughly 16k trades/day; 93 % are funding taken.
  - About 90 % are 2-day loans, at roughly 6 % APR.
  - The FRR at the time was ~9.2 % APR, above most traded rates.
- Reconstructed FRR tickers: 729 rows (`origin=reconstructed`). **Next-step 2 is done.**
- **Step 3 is done.** The first real-data backtest ran on 2026-11-03 in about 1 minute.
  - Window: 2026-10-05 → 11-03, 483k trades, 698 reconstructed FRR tickers, no order books.
  - Output goes to `experiments/outputs/real_baselines_30d/`, which is git-ignored. Re-run step 3 to regenerate it.
  - Results are SIMULATED and RECONSTRUCTED. Net annualised yield, % (conservative / neutral / optimistic):

    | strategy | cons. | neutral | optim. |
    |---|---|---|---|
    | frr_2d (baseline) | 2.32 | 2.58 | 2.72 |
    | fixed 0.00015/d 2d | 4.27 | 4.42 | 4.29 |
    | fixed 0.00020/d 2d | 4.61 | 4.92 | 4.93 |
    | fixed 0.00025/d 120d | 4.44 | 5.95 | 6.02 |
    | adaptive ladder | 5.29 | 5.91 | 6.02 |

  - **Caveat — the FRR baseline is probably underestimated.** The simulator fills FRR offers only when trades print at or above the FRR. The FRR (~9 % APR) sat above most trade rates (~6 % APR), so FRR utilisation came out at only ~27–31 %. Real Bitfinex FRR offers are matched by FRR-based loans, which the model does not represent. So "excess vs FRR" is not trustworthy yet. Comparisons among the fixed-rate and ladder strategies are more meaningful.
  - **Other caveats:**
    - only one month of data, with no out-of-sample split;
    - no queue information;
    - early-repayment hazards are assumptions;
    - the ladder's fill ratio is low in the conservative scenario (37 %) because it reprices often.
  - Suggested follow-ups:
    - model FRR-offer matching better, or use a fixed-rate baseline that sits at the median traded rate;
    - extend the backfill to 6–12 months and evaluate walk-forward;
    - add an `optimization/` sweep over fixed rate × period.

## Next steps (in order)

1. **Rebuild data if the container was reset.**
   ```bash
   export FUNDPILOT_DATABASE_URL=postgresql+psycopg://fundpilot:fundpilot@localhost:5432/fundpilot_dev
   uv run fundpilot db upgrade
   for a in "stats --symbol fUST" "candles --symbol fUST --timeframe 1h" "volatility --symbol tBTCUSD" "volatility --symbol tETHUSD" "trades --symbol fUST"; do uv run fundpilot backfill $a --start 2026-10-04T00:00:00+00:00; done
   ```
2. **Reconstruct FRR tickers.**
   `uv run fundpilot reconstruct-tickers --symbol fUST --start 2026-10-04T00:00:00+00:00`
3. **Run the first real-data backtest.**
   `uv run fundpilot backtest experiments/real_baselines_30d.toml --output experiments/outputs/real_baselines_30d`
   - Check memory and runtime: 30 days × 5-minute steps, `trade_lookback_seconds = 3600`.
   - Results are SIMULATED. Label them RECONSTRUCTED: no historical order book, so no queue information.
   - Report per scenario. Conclusions must hold under the conservative scenario.
4. **Open a PR** for this branch into `main`; the user approves merges. Then run ML (`experiments/ml_*.toml`) and RL (`experiments/rl_comparison.toml`) on the real window with `allow_synthetic = false`. Those configs need `allow_reconstructed`/`require_book` support (only backtest has it today; ml/rl pipelines use `FeedConfig` defaults). Adding it is a small change in `ml/pipeline.py` and `cli.cmd_rl`.
5. **Long-running collection** (needs a persistent host, not this container): `fundpilot collect` + `fundpilot stream` (or `docker compose up`). This accumulates real order books and tickers for book-aware backtests and paper trading.
6. **Optional:** allow `support.bitfinex.com` to verify A12–A15; optional `optimization/` module (SciPy).

## Decisions and constraints from the user

- Phase 7 (live trading) is **not** to be started. The user has no Bitfinex API key yet, and live execution requires explicit approval.
- Merges into `main` need the user's OK. PR #3 was approved and merged.
- Reply to the user in Traditional Chinese.
- Separate verified facts from inference; cite sources for time-sensitive claims.
- Never present simulated results as real returns.

## Aave V3 USDT/USDC strategy (started 2026-11-04, in progress)

User request: develop an Aave DeFi strategy for **USDT and USDC**. Constraints are the same as for Bitfinex: no private keys, no live transactions, dry-run / simulation only.

**Verified sources:**
- Aave V3 contracts from `aave-dao/aave-v3-origin` (main), fetched via raw.githubusercontent.com:
  - Interest model `DefaultReserveInterestRateStrategyV2.calculateInterestRates`: borrow rate is base + slope1·U/Uopt below the optimal point, plus slope2 above it. Supply rate = borrow rate × supply usage × (1 − reserve factor).
  - Supply interest accrues linearly per index update (`MathUtils.calculateLinearInterest`); `SECONDS_PER_YEAR` = 365 days.
  - `AaveProtocolDataProvider.getReserveData` returns 12 fields: totalAToken idx 2, totalVariableDebt idx 4, liquidityRate idx 5, variableBorrowRate idx 6, indexes idx 9–10, lastUpdate idx 11.
  - `ReserveConfiguration` bit positions: reserve factor at 64, supply cap at 116, decimals at 48, active 56, frozen 57, paused 60.
- Addresses from `bgd-labs/aave-address-book` (main): Ethereum, Arbitrum, Base, Optimism, Polygon and Avalanche. Base has USDC only.

**Blocked network:** no Aave/DefiLlama/RPC host is reachable from this session. Needed hosts: any Ethereum/L2 JSON-RPC endpoint (e.g. ethereum-rpc.publicnode.com) and optionally yields.llama.fi for history. The user must allow them.

**Plan / module layout:** `src/fundpilot/defi/aave/`
- markets registry, ray/rate math, keccak + ABI helpers
- JSON-RPC reader of the reserve state
- table `defi_reserve_snapshots` (migration 0005)
- cost- and impact-aware allocator: water-filling across markets with the post-deposit rate from the model, a gas/bridge cost hurdle, and utilisation/supply-cap/depeg guards
- backtester with a withdrawal-liquidity constraint, compared against static and naive APY-chasing baselines
- CLI `fundpilot aave ...` and `docs/aave.md`

Check `git log` for which parts landed.

**Status 2026-11-04: implemented and tested.** Full suite: 257 tests pass, including PostgreSQL. Docs: `docs/aave.md`.
- Commands: `fundpilot aave markets | snapshot | plan | backtest`.
- Configs: `experiments/aave_usdt.toml` and `experiments/aave_usdc.toml`.
- Migration `0005`: `defi_reserve_snapshots`.

**Not done yet:**
1. Live reads. The user must allow at least one JSON-RPC host per chain. Then set `FUNDPILOT_AAVE_RPC_URLS` and run `fundpilot aave snapshot` hourly (cron or a loop) to build history.
2. Historical backfill. The DefiLlama yields API (`yields.llama.fi/chart/<pool>`) could seed history if allowed; its response shape is unverified, so verify it before writing a parser.
3. Real-data backtest of the allocator against the baselines, once snapshots exist.
4. USDT↔USDC rotation (swap cost + depeg guard), which is not implemented.
5. Optional: wire Aave snapshots into the dashboard and agent analytics.

**Synthetic-demo observation** (not evidence): the naive APY chaser loses heavily to gas/bridge costs on small capital. The 92 % utilisation guard costs yield on synthetic data, because withdrawal lock-ups rarely bind there. Do not tune on synthetic data.

**Update 2026-11-04 (user preference): low-gas chains only.**
- Ethereum mainnet is excluded by default: `LOW_GAS_CHAINS` = polygon, arbitrum, base, optimism, avalanche.
- Fixed in the same change:
  - same-chain wallet deposits were wrongly charged a bridge fee;
  - deploying idle capital no longer decays with the spread half-life;
  - the backtester now raises if a policy targets a market without data. Before, it silently earned 0 %.
- Synthetic demo (low-gas chains): static 4.9 %, chaser 9.5 % (66 moves), allocator 7.6 % (22 moves). This depends on the assumed bridge cost of 5; not evidence.
- RPC hosts still needed for the chosen chains, e.g. publicnode endpoints for Polygon/Arbitrum/Base/Optimism/Avalanche (names unverified).
