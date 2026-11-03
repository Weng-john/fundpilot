# Risk management

**Status: design only (Phase 3). No risk manager code exists yet.**

The risk manager will be an independent, deterministic component between any decision source
(strategy, ML/RL model, AI agent, human) and the execution gateway. Planned controls:

| Control | Notes |
|---|---|
| Max capital allocation, max single-offer size | per currency and total |
| Max funding duration | caps capital lock-up |
| Min acceptable rate | daily fraction, net of fees |
| Min available liquidity | keep a buffer unlent |
| Max concentration | by duration bucket / maturity date |
| Market-data staleness | uses `data_quality_issues` + latest timestamps from Phase 1 |
| API failure detection | uses `ingestion_runs` failure rates from Phase 1 |
| Stablecoin depeg / exchange operational alerts | block new offers |
| Emergency suspension | stops *new* offers and cancels *unfilled* offers |
| Manual approval | for sensitive or large operations |

**Emergency suspension cannot recall active funding credits.** Per Bitfinex support
documentation (seen via search, not read directly), a lender cannot take back funding once it
has been taken by a borrower; capital is released only when the borrower repays or the term
ends. Reports and UI must state this explicitly.
