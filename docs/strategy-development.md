# Strategy development guide

## Contract

```python
class Strategy(Protocol):
    @property
    def name(self) -> str: ...
    def decide(self, market: MarketState, portfolio: PortfolioState) -> Decision: ...
```

* `MarketState` (`fundpilot.strategies.base`): `as_of`, latest `ticker` and `book` observed at
  or before `as_of`, recent `trades` (all `executed_at <= as_of`), and a `stale` flag.
* `PortfolioState` (`fundpilot.core.portfolio`): `available`, unfilled `offers`, active
  `credits` (with `expires_at`), `pending_interest`.
* `Decision`: offers to `place` (`OfferRequest`: rate type FIXED/FRR, daily rate, period,
  amount, tag), offer ids to `cancel`, free-text `notes` for explanations.

Strategies **never** call an exchange or the database. They may keep internal state between
calls (rolling statistics); because they only ever receive the current state, they cannot see
the future. The backtester builds a fresh instance per run.

## Constraints

`strategies.constraints.enforce()` is applied to every decision, identically for all strategies:
cancels first (unknown ids dropped; active credits cannot be cancelled), then offers in order,
capped by remaining capital, amounts rounded **down** to 8 decimals, rejected if below the minimum
offer (150), outside 2–120 days, or with an invalid rate. Rejections are counted in reports.

## Baselines

| id | Class | Behaviour |
|---|---|---|
| `fixed_rate` | `FixedRateStrategy` | all idle capital at one fixed rate/period; never reprices |
| `frr` | `FRRStrategy` | all idle capital as variable FRR offers |
| `auto_renew` | `AutoRenewStrategy` | approximation of exchange auto-renew: re-offer idle capital above `keep_amount` at FRR or a fixed rate |
| `adaptive_ladder` | `AdaptiveFundingLadder` | short/medium/long buckets (2/30/120 d); tilts to long when the reference rate is high vs. its own history, to short when low; prices each bucket from recent trade-rate quantiles (FRR fallback); reprices stale offers; optional liquidity reserve |
| `orderbook_aware` | `OrderBookAwareStrategy` | undercuts the ask level where `depth_ahead` supply would be ahead; uses the best bid if it pays more; reprices stale offers |

`FRRStrategy` and `AutoRenewStrategy` with default parameters behave identically in the
simulator; both are kept because they map to different exchange features.

## Adding a strategy

1. Implement the protocol in `src/fundpilot/strategies/`.
2. Register a factory in `backtesting/experiment.py::STRATEGY_TYPES`.
3. Add unit tests with hand-built `MarketState`s (`tests/helpers.py`).
4. Compare against the baselines with an experiment TOML under **all three** scenarios.
