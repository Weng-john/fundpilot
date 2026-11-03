# Strategy development guide

**Status: planned for Phase 2. The interface below is a design target, not yet implemented.**

Every strategy will receive the same immutable `MarketState` (built from `core.models`:
latest ticker, book snapshot, recent stats/trades) and `PortfolioState` (capital, active
offers/credits, maturities), and return a structured `Decision` (offers to place / cancel /
replace, each with rate, period, amount, rationale). Strategies never call an exchange.

Baselines to implement first: fixed-rate, FRR-based, auto-renew equivalent, adaptive funding
ladder, order-book-aware rate selection. Constraints from Bitfinex (min offer size, 2–120 day
period, fees) must be applied identically to all strategies.
