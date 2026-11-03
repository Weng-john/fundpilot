# Risk management

`fundpilot.risk.RiskManager` is an independent, deterministic layer between **every** decision
source (strategy, future ML/RL model, AI agent, human) and the execution gateway. It never adds
exposure: it can drop or shrink offers, hold them for manual approval, add cancels of
*unfilled* offers, and block new offers.

## Controls (`RiskLimits`)

| Control | Field | Effect |
|---|---|---|
| Max capital allocation | `max_allocation_fraction` | (offered + lent + new) ≤ fraction × total; offers shrunk |
| Max funding duration | `max_period_days` | longer offers rejected |
| Max single offer | `max_single_offer` | offer shrunk |
| Min acceptable rate | `min_daily_rate` | fixed offers below rejected; FRR offers rejected if current FRR is below |
| Min available liquidity | `min_liquidity_fraction` | keeps that share of total capital idle; offers shrunk |
| Max concentration | `max_period_concentration` | per-period (offered + lent) share; offers shrunk |
| Market-data staleness | `max_data_age` + `market.stale` | blocks new offers |
| API failure detection | `max_consecutive_api_failures` | blocks new offers |
| Stablecoin depeg | `depeg_threshold` on a price feed (e.g. `tUSTUSD`, unverified code) | blocks new offers |
| Exchange operational status | `platform/status` | blocks new offers when not operational |
| Emergency suspension | persistent `control_flags.emergency_stop` | blocks new offers |
| Manual approval | `approval_threshold` | larger offers go to `approval_requests` |

When new offers are blocked and `cancel_unfilled_on_block` is true (default), all **unfilled**
offers are cancelled.

## What emergency suspension does *not* do

**Active funding credits cannot be recalled.** Capital lent to a borrower returns only when the
borrower repays or the term ends (Bitfinex support FAQ, seen via search:
<https://support.bitfinex.com/hc/en-us/articles/214441485>). Every emergency alert and CLI message
states this explicitly; nothing in FundPilot claims otherwise.

## Operating the controls

```bash
uv run fundpilot emergency-stop --reason "depeg rumours" --by alice
uv run fundpilot alerts
uv run fundpilot resume --confirm --by alice
uv run fundpilot approvals list
uv run fundpilot approvals approve 12 --by alice     # or: reject 12
```

Approved requests expire after `approval_ttl` (default 1 h) and are re-checked by the risk manager
at execution time; an approval is executed at most once.

## Using the risk layer in backtests

`RiskManager(limits).as_gate()` plugs into `FundingSimulator(..., gate=...)`, so backtests can apply
exactly the same limits as paper trading. Offers needing approval are treated as not approved.
