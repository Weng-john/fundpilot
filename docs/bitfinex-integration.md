# Bitfinex API integration guide

## Verification status — read first

This adapter was written in an environment where `docs.bitfinex.com`,
`support.bitfinex.com` and `api-pub.bitfinex.com` were **not reachable**. No live
API call has been made. Everything below is classified as:

| Tag | Meaning |
|---|---|
| **SDK** | Taken from the official `bitfinex-api-py` 4.0.0 source (`bfxapi/types/serializers.py`, `bfxapi/rest/_interfaces/*`, `bfxapi/_client.py`), published by Bitfinex on PyPI. Second-hand but authored by Bitfinex. |
| **Search** | Seen in web-search summaries of official Bitfinex docs / support pages; page not read directly. |
| **Unverified** | Recalled or inferred; must be confirmed before research or live use. |

Open an *Exchange API verification* issue to promote anything to verified.

## Hosts

| Purpose | URL | Status |
|---|---|---|
| Public REST | `https://api-pub.bitfinex.com/v2` | SDK |
| Authenticated REST | `https://api.bitfinex.com/v2` | SDK |
| Public WebSocket | `wss://api-pub.bitfinex.com/ws/2` | SDK |
| Authenticated WebSocket | `wss://api.bitfinex.com/ws/2` | SDK |

## Public endpoints used (Phase 1)

| Method | Path | Response row | Status |
|---|---|---|---|
| `platform/status` | GET | `[STATUS]` (1 = operative) | SDK |
| Funding ticker | `ticker/{fSYM}` | 16 fields: `FRR, BID, BID_PERIOD, BID_SIZE, ASK, ASK_PERIOD, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_RELATIVE, LAST_PRICE, VOLUME, HIGH, LOW, _, _, FRR_AMOUNT_AVAILABLE` | SDK |
| Aggregated book | `book/{fSYM}/{P0..P4}?len={1,25,100}` | `[RATE, PERIOD, COUNT, AMOUNT]` | SDK |
| Raw book | `book/{fSYM}/R0?len=` | `[OFFER_ID, PERIOD, RATE, AMOUNT]` | SDK |
| Funding stats | `funding/stats/{fSYM}/hist?start&end&limit` | 12 fields; idx 0 `MTS`, 3 `FRR`, 4 `AVG_PERIOD`, 7 `FUNDING_AMOUNT`, 8 `FUNDING_AMOUNT_USED`, 11 `FUNDING_BELOW_THRESHOLD` | SDK (indices 4/7/8 also Search) |
| Funding trades | `trades/{fSYM}/hist?limit&start&end&sort` | `[ID, MTS, AMOUNT, RATE, PERIOD]` | SDK |
| Candles | `candles/{key}/hist?limit&start&end&sort` | `[MTS, OPEN, CLOSE, HIGH, LOW, VOLUME]` | SDK |

| Trading candles (volatility) | `candles/trade:{tf}:{tPAIR}/hist` | same 6-field layout | SDK |
| Trading ticker (depeg check) | `ticker/{tPAIR}` | 10 fields, `LAST_PRICE` at index 6 | SDK |

## Public WebSocket (`exchanges/bitfinex/ws.py`)

| Item | Detail | Status |
|---|---|---|
| Subscribe | `{"event":"subscribe","channel":"ticker"\|"trades"\|"book","symbol":"fUST",...}`; book adds `prec`, `freq`, `len` (1/25/100/250) | SDK |
| Data frames | `[chanId, payload]`; heartbeat `[chanId,"hb"]`; book checksum `[chanId,"cs",n]` | SDK |
| Funding trades | snapshot = list of `[ID,MTS,AMOUNT,RATE,PERIOD]`; updates `[chanId,"fte"\|"ftu",row]` | SDK |
| Funding book | snapshot = list of `[RATE,PERIOD,COUNT,AMOUNT]`, update = one row | SDK |
| Reconnect request | `{"event":"info","code":20051}` | SDK |
| Level removal | update with `COUNT = 0` removes the level | Unverified |
| Maintenance | info codes 20060 (start) / 20061 (end) | Unverified |

The stream reconnects with exponential back-off, treats 30 s of silence as a dead connection,
clears book state on reconnect and re-subscribes. `fundpilot stream` persists trades continuously
and ticker/book snapshots every `--flush-every` seconds.

## Assumptions that need verification

| # | Assumption | Where it is used | Status |
|---|---|---|---|
| A1 | Tether USDt funding symbol is `fUST` | default `FUNDPILOT_SYMBOLS` | Unverified |
| A2 | Book/ticker/trade/candle rates are daily decimal fractions | `parsers.py` (no scaling) | Unverified |
| A3 | Funding-stats `FRR` is reported as FRR/365; multiply by 365 for daily | `FUNDPILOT_BITFINEX_FUNDING_STATS_FRR_SCALE` (raw value always stored in `frr_raw`) | Unverified |
| A4 | Funding book: `AMOUNT > 0` = lender offer (ask), `< 0` = borrower bid | `BookSide` mapping | Unverified |
| A5 | Funding trade `AMOUNT` sign semantics | stored signed, not interpreted | Unverified |
| A6 | Funding candle key `trade:{tf}:{fSYM}:a30:p2:p30` (aggregate) or `:p{N}` | `FUNDPILOT_BITFINEX_CANDLE_PERIOD_SPEC` | Unverified |
| A7 | Candle timeframes `1m 5m 15m 30m 1h 3h 6h 12h 1D 1W 14D 1M` | `symbols.CANDLE_TIMEFRAMES` | Unverified |
| A8 | Rate limit 10–90 req/min per endpoint; exceeding blocks the IP for 60 s; JSON error `ERR_RATE_LIMIT` | `TokenBucket` default 15/min, 60 s cooldown | Search ([requirements & limitations](https://docs.bitfinex.com/docs/requirements-and-limitations)) |
| A9 | Rate-limit error code `11010` | client detects code *or* "ratelimit" in message | Unverified |
| A10 | Error codes 10000/10001/10020/10100 | non-retryable mapping | SDK |
| A11 | Page-size maxima (stats 250, trades/candles ≥ 1000) | backfill `page_limit` defaults | Unverified |
| A12 | Funding period range 2–120 days | quality checks | Search ([What is Margin Funding](https://support.bitfinex.com/hc/en-us/articles/214441185-What-is-Margin-Funding)) |
| A13 | Minimum offer USD 150 equivalent | (Phase 3+ strategy constraints) | Search (same) |
| A14 | Lender fee 15 % of interest, 18 % for hidden offers | (Phase 2 fee model) | Search ([fees](https://support.bitfinex.com/hc/en-us/articles/213919589-What-fees-do-you-charge-)) |
| A16 | USDt/USD trading pair code `tUSTUSD` (depeg check) and `tBTCUSD`/`tETHUSD` (volatility) | paper config, `FUNDPILOT_VOLATILITY_PAIRS` | Unverified |
| A15 | Interest is calculated to the second | `accrued_interest` | Search ([interest calc](https://support.bitfinex.com/hc/en-us/articles/213918989-How-is-Funding-interest-calculated-on-Bitfinex)) |

### How to verify (once the hosts are reachable)

1. `curl https://api-pub.bitfinex.com/v2/ticker/fUST` — confirm A1/A2 and the 16-field layout.
2. Compare `funding/stats/fUST/hist?limit=1` FRR × 365 with the ticker FRR (A3).
3. Inspect `book/fUST/P0?len=25` signs against the Bitfinex UI (A4).
4. Request a candle key with both `a30:p2:p30` and `p30` (A6).

## Authenticated endpoints (Phase 7 — NOT implemented)

From the SDK, for later reference only: `auth/r/wallets`, `auth/r/funding/offers[/{sym}]`,
`auth/w/funding/offer/submit`, `auth/w/funding/offer/cancel`, `auth/w/funding/offer/cancel/all`,
`auth/r/funding/credits[/{sym}]`, `auth/r/funding/trades/{sym}/hist`, `auth/r/ledgers/...`.
Signature: HMAC-SHA384 over `/api/v2/{path}{nonce}{body}` (SDK). FundPilot will never call
withdrawal or transfer endpoints, and API keys must be created **without** withdrawal permission.

## Normalisation rules

* Rates → daily decimal fraction (`Decimal`). Raw exchange value is kept whenever a scale is applied.
* JSON numbers are parsed with `parse_float=Decimal`; floats never enter financial code.
* Timestamps (`MTS`, ms) → aware UTC `datetime`.
* Book amounts → absolute value + explicit `side`.
