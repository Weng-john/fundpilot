# Bitfinex API integration guide

## Verification status

Verified on **2026-11-03** against the official documentation (fetched as Markdown from
`docs.bitfinex.com/...md`) and live public API responses. Tags:

| Tag | Meaning |
|---|---|
| **Docs** | Stated on an official docs.bitfinex.com page (URL given) |
| **Live** | Confirmed with a real `api-pub.bitfinex.com` response |
| **SDK** | Official `bitfinex-api-py` 4.0.0 source |
| **Search** | Only seen in web-search summaries of support.bitfinex.com (not reachable here) |

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

## Assumptions and their status

| # | Statement | Status | Source |
|---|---|---|---|
| A1 | `UST` is Tether USDt and a margin-funding currency (`fUST`) | **Verified** | Live: `conf/pub:map:currency:label` → `UST: "Tether USDt"`; `conf/pub:list:currency:margin` contains `UST` |
| A2 | Ticker/book/trade/candle rates are daily decimal fractions | **Verified** | Live: ticker FRR 0.00025254 ≈ funding-stats FRR 6.9e-7 × 365 |
| A3 | Funding-stats FRR = FRR/365 (× 365 for daily) | **Verified** | Docs [funding stats](https://docs.bitfinex.com/reference/rest-public-funding-stats) + Live |
| A4 | Funding book `AMOUNT > 0` = ask (offer), else bid | **Verified** | Docs [book](https://docs.bitfinex.com/reference/rest-public-book) |
| A5 | Funding trade `AMOUNT` > 0 = funding *provided* by the taker, < 0 = funding *taken* | **Verified** | Docs [ws trades](https://docs.bitfinex.com/reference/ws-public-trades). The simulator now lets only taken funding fill resting offers (optimistic scenario excepted) |
| A6 | Funding candle keys `:pN` or aggregate `:a30:p2:p30` (UI-equivalent) | **Verified** | Docs [candles](https://docs.bitfinex.com/reference/rest-public-candles) + Live |
| A7 | Timeframes 1m 5m 15m 30m 1h 3h 6h 12h 1D 1W 14D 1M | **Verified** | Docs candles |
| A8 | Rate limits per endpoint; 60 s IP block; body `{"error": "ERR_RATE_LIMIT"}` | **Verified** | Docs [requirements](https://docs.bitfinex.com/docs/requirements-and-limitations): ticker 90, book 240, candles 30, trades 15, platform/status 30 req/min; funding stats unpublished (10/min used). Client fixed to detect the object form |
| A9 | Rate-limit error code 11010 | Not documented | kept only as an extra defensive check |
| A10 | `["error", code, msg]` for other errors | **Verified** | Live: `ticker/fXYZ` → `["error",10020,"symbol: invalid"]` |
| A11 | Page sizes: trades/candles ≤ 10000, funding stats ≤ 250 | **Verified** | Docs trades, candles, funding stats |
| A12 | Funding period 2–120 days | Search | support.bitfinex.com not reachable from the dev environment |
| A13 | Minimum offer USD 150 equivalent | Search | same |
| A14 | Lender fee 15 % (18 % hidden) | Search | same |
| A15 | Interest accrues per second | Search | same |
| A16 | Pairs `tUSTUSD`, `tBTCUSD`, `tETHUSD` | **Verified** | Live ticker / candles |
| WS | `count = 0` deletes a level; info 20051/20060/20061; platform status flag; heartbeat 15 s; ≤ 20 connections/min (api-pub); ≤ 30 subscriptions/connection | **Verified** | Docs [ws general](https://docs.bitfinex.com/docs/ws-general), [ws books](https://docs.bitfinex.com/reference/ws-public-books), requirements |

Additional documented fields: funding ticker index 16 `FIRST_TRADE`; trading ticker index 10
`FIRST_TRADE` (parsers accept the extra fields).

Observed on live data (2026-11-03, informational): long-period borrower bids at the FRR often
sit above short-period lender asks, so a "crossed" top of book across periods is normal. The
order-book-aware strategy now only compares bids/asks whose period fits its own.

### How to re-verify

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
