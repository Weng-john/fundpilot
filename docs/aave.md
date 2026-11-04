# Aave V3 USDT / USDC supply strategy

**Read-only and simulation only.** FundPilot holds no private keys and sends no transactions.
`fundpilot aave plan` prints a *dry-run* recommendation. Executing on-chain would be a
separate, explicitly approved phase, like Bitfinex live trading (Phase 7).

## Verified protocol facts (sources fetched 2026-11-04)

| Topic | Fact | Source |
|---|---|---|
| Interest model | borrow = base + slope1·U/Uopt (U ≤ Uopt), else base + slope1 + slope2·(U−Uopt)/(1−Uopt); supply = borrow × U × (1 − reserveFactor) | `aave-dao/aave-v3-origin` `misc/DefaultReserveInterestRateStrategyV2.sol` |
| Rate units | annual rates in ray (1e27); `SECONDS_PER_YEAR = 365 days`; supply accrues linearly per index update | `protocol/libraries/math/MathUtils.sol`, `logic/ReserveLogic.sol` |
| Data provider | `getReserveData` 12 fields (totalAToken idx 2, totalVariableDebt 4, liquidityRate 5, variableBorrowRate 6); stable debt deprecated since v3.2, unbacked since v3.4 | `helpers/AaveProtocolDataProvider.sol` |
| Config bits | reserve factor bit 64 (bps), supply cap bit 116 (whole tokens, 0 = none), decimals 48, active 56, frozen 57, paused 60 | `configuration/ReserveConfiguration.sol` |
| Supply cap check | `(scaledTotalSupply + amount + accruedToTreasury) · index ≤ cap · 10^decimals` | `logic/ValidationLogic.sol` |
| Addresses | Pool, PoolDataProvider, underlying and aToken for USDT/USDC on Ethereum, Arbitrum, Base (USDC only), Optimism, Polygon, Avalanche | `bgd-labs/aave-address-book` `src/AaveV3<Chain>.sol` |

The main branch is v3.4+. Older deployments may lack `getVirtualUnderlyingBalance` or
`getInterestRateDataBps`; the reader then falls back to `aTokens − debt` or skips the model, and
logs a warning.

**Inference, not verified:**
- Per-second-compounded APY is a presentation convention (`apr_to_apy`).
- Gas and bridge costs in the configs are placeholders. Update them for real use.

## Components

| Module | Purpose |
|---|---|
| `defi/abi.py` | pure-Python Keccak-256 (tested against known vectors), selectors, word decoding |
| `defi/rpc.py` | minimal JSON-RPC client (`eth_call`, `eth_blockNumber`) |
| `defi/aave/markets.py` | reserve registry |
| `defi/aave/math.py` | rate model, own-impact supply APR, APY, linear accrual |
| `defi/aave/reader.py` | `AaveProtocolDataProvider` + rate-strategy reads → `ReserveSnapshot` |
| `defi/aave/store.py` | `defi_reserve_snapshots` table (migration `0005`), latest and point-in-time series |
| `defi/aave/strategy.py` | allocator (below) |
| `defi/aave/backtest.py` | backtester, baseline policies, synthetic series |

## Chain selection

By user preference (2026-11-04), the default chains exclude Ethereum mainnet because of gas: `LOW_GAS_CHAINS = polygon, arbitrum, base, optimism, avalanche`. Set them via:
- `chains = [...]` in experiment TOMLs;
- `--chains` on `fundpilot aave plan`;
- `AllocatorConfig(allowed_chains=None)` to allow every chain.

USDT reserves: Polygon (address-book symbol USDT0), Arbitrum, Optimism, Avalanche (USDt). Base has USDC only.

**Costs are assumptions.**
- The per-transaction gas values in the configs are placeholders.
- The bridge cost dominates on low-gas chains (default 5 per cross-chain move), so set it from the bridge you would actually use.
- On low-gas chains, frequent rebalancing is cheap. The synthetic demo then favours the naive APY chaser: 9.5 % vs 7.6 % net, with 66 vs 22 moves. This depends entirely on the assumed bridge cost and on synthetic data, so it is not a conclusion.

## Allocator

For one stablecoin at a time (USDT or USDC):

1. Remove our own positions from each reserve's state. Then allocate capital in chunks to the market with the highest **marginal income**, `(x+c)·apr(x+c) − x·apr(x)`. The `apr` uses the reserve's own rate model, so large deposits that compress a pool's rate get spread across markets.
2. **Guards.** No new capital into reserves that are:
   - frozen, paused or inactive;
   - above `max_utilization` (default 92 %, where withdrawals risk being blocked);
   - beyond 98 % of their remaining supply-cap headroom.
   Withdrawals are limited by available liquidity.
3. **Costs and hysteresis.** Each move costs gas on the source and destination chains, plus `bridge_cost` across chains. Rate spreads mean-revert, so the expected gain is the APR difference integrated over `horizon_days` with a `spread_half_life_days` decay (default 3 days). A plan is adopted only if gain − cost > `min_gain`.

## Backtester

Assumptions:
- Input snapshots exclude our capital.
- Our deposits move the APR through the rate model, with borrow demand held constant.
- Interest accrues linearly between steps.
- Withdrawals are capped by the *observed* liquidity (conservative default). `with_us` lets our own deposit always be withdrawn.
- Costs come out of moved amounts.

Not modelled:
- smart-contract, oracle and bridge failures;
- stablecoin depegs;
- governance parameter changes inside a window;
- reward incentives.

Baselines:
- `static` (one market, never moves);
- `chaser` (highest observed APR, ignores impact and costs).

## Usage

```bash
# read-only data collection (needs JSON-RPC endpoints reachable from your network)
export FUNDPILOT_AAVE_RPC_URLS='{"ethereum": "https://ethereum-rpc.publicnode.com", "arbitrum": "https://arbitrum-one-rpc.publicnode.com"}'
uv run fundpilot aave markets
uv run fundpilot aave snapshot                     # run e.g. hourly to build history
uv run fundpilot aave plan --symbol USDC --amount 50000 --chain ethereum
uv run fundpilot aave backtest experiments/aave_usdc.toml           # on stored snapshots
uv run fundpilot aave backtest experiments/aave_usdt.toml --synthetic-demo
```

Synthetic demo results (illustrative only, not market evidence) are discussed under *Chain selection*. Which policy wins depends on the data. The utilisation guard deliberately gives up yield
in exchange for withdrawal safety, which synthetic data does not reward.
