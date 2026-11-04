# Research: OKX Simple Earn (Flexible and Fixed) for USDT/USDC

Status: **verified on 2026-11-04** against the official API reference
(`https://www.okx.com/docs-v5/en/`, "Public borrow info" / "Public borrow history"), the docs change
log (`/docs-v5/log_en/`) and live public responses. Implementation lives in
`fundpilot.exchanges.okx`:
- `fundpilot okx summary`
- `fundpilot okx backfill` (table `cex_earn_rates`, migration `0006`)
- `fundpilot compare-yields`

### Corrections to the first draft

- **`rate` is the *borrowing* rate, and `lendingRate` is what lenders earn** (docs). The summary fields `avgRate`, `preRate` and `estRate` are all *borrowing* rates.
- The third-party CSV quoted in the first draft recorded `rate`, so it **overstated** lender yield.
- **Simple Earn Fixed has no API.** Its endpoints went offline on **2025-03-03**: `fixed-loan/lending-offers`, `lending-apy-history`, `lending-order` and others (docs change log). A live call to `lending-offers` returns 403. In the current docs, Fixed appears only as ledger bill types, so Fixed can only be used manually in the app or web UI.
- History is available from 2021-12-14 (docs), at most 100 rows per page, paginated with `after`. Public endpoints allow 6 requests/s per IP.
- Requests with some default library user agents get HTTP 403. The client sends its own UA.

### Fee treatment (inference)

`lendingRate / rate` had a median of **0.851 (USDT) / 0.849 (USDC)** over 2026-08-05 → 11-04. That matches the FAQ formula "× 85 %", so `lendingRate` is believed to be **net** of OKX's 15 % share. The docs do not state this explicitly.

### Measured net yields, 30 days to 2026-11-04 (`fundpilot compare-yields`, % per year)

| Venue | n | mean | median | p10 | p90 |
|---|---|---|---|---|---|
| Bitfinex fUST 2-day, taken trades, × 0.85 | 337,372 trades | 5.66 | 5.27 | 3.94 | 7.67 |
| OKX Flexible USDT, `lendingRate` | 720 h | 3.19 | 2.93 | 2.77 | 3.84 |
| OKX Flexible USDC, `lendingRate` | 720 h | 3.62 | 3.04 | 2.83 | 4.65 |
| Aave V3 Polygon / Arbitrum USDT/USDC | 1 snapshot each | 3.03–3.75 | | | |

Over 90 days, OKX Flexible averaged 2.98 % (USDT) and 2.93 % (USDC).

The Bitfinex figure uses rates of loans that *did* execute. Our own offers would not always fill:
the simulated 30-day ladder earned 5.3–6.0 % net depending on the execution scenario. Even so, Bitfinex
funding paid roughly **2–2.5 percentage points more** than OKX Flexible or Aave in this window.
OKX Flexible's advantages are instant redemption and no gas. It fits a liquidity buffer, not yield
maximisation.

---

## Original research notes (first draft, partly superseded above)

## 1. Simple Earn Flexible: how it works

| Item | Detail | Label |
|---|---|---|
| Matching | Every hour, lending bids are sorted by minimum APR (low → high) until borrowing demand is met. The highest accepted bid sets the hour's market APR. | [OKX] [Introduction & rules](https://www.okx.com/help/introduction-to-okx-savings-and-its-rules) |
| Your min APR vs the market | below → lent at the market APR; equal → partially or not lent; above → not lent and no interest that hour | [OKX] same page |
| Earnings formula | hourly return = lent amount × APR / 365 / 24 × **85 %** (OKX keeps 15 %, the same as Bitfinex's lender fee) | [OKX] [FAQ](https://www.okx.com/en-ar/help/simple-earn-faq) |
| Liquidity | instant subscription and redemption | [OKX] FAQ |
| Rule change for stablecoins | From 2025-11-13 (UTC+8), if your min APR ≤ the borrowing rate you are eligible, but the final APR can be *below* your minimum when the pool is oversupplied (example: 5 % min and 5 % borrow rate → 4 %) | [OKX] [Announcement](https://www.okx.com/en-us/help/announcement-updated-stablecoin-lending-apr-logic-for-simple-earn-flexible) |
| Min-APR field removal | One OKX page says min-APR bidding will be removed from 2026-08-27 for new and existing subscriptions. A third-party guide says the same. The dates are inconsistent; verify in the app. | [OKX] [Updates](https://www.okx.com/en-us/help/okx-earn-simple-earn-flexible-updates), [3rd] [cjfjpz182/okx-simple-earn](https://github.com/cjfjpz182/okx-simple-earn) |
| Bonus | 10 % bonus APR on the first 1,000 USDT/USDC for 180 days (a past campaign; may not apply now). Third parties quote 500, which conflicts. | [OKX] [Bonus policy](https://www.okx.com/en-us/help/simple-earn-flexible-bonus-policy-update) |
| Auto-Earn | idle funds join the pool without a min APR; USDT requires a market APR ≥ 1 % | [OKX] [Auto Earn rules](https://www.okx.com/en-us/help/auto-earn-rules) |

## 2. Simple Earn Fixed

| Item | Detail | Label |
|---|---|---|
| Product | fixed rate for a fixed term. After matching, interest accrues hourly and is paid at maturity. USDT and USDC supported; example term 30 days. | [OKX] [Simple Earn Fixed](https://www.okx.com/en-us/help/get-to-know-okx-simple-earn-fixed-product) |
| Early exit | generally not possible before maturity. One third-party claim says early exit returns principal without interest (unverified). | [OKX] same page; [3rd] |
| Current APR | **unknown.** Third parties cite 4 % (90-day USDT) and 5–8 %; neither is reliable | [3rd] |
| API | A changelog lists `GET /api/v5/finance/fixed-loan/lending-offers` and related lending-order endpoints as **going offline**, with an "adjustment" section moving some routes. CCXT still lists the path. Current status unknown. | [OKX] [API change log](https://www.okx.com/docs-v5/log_en/), [3rd] [CCXT](https://docs.ccxt.com/docs/exchanges/okx/implicit-api) |

## 3. Public API for Flexible (to verify once reachable)

| Endpoint | Purpose | Fields (unverified) | Label |
|---|---|---|---|
| `GET /api/v5/finance/savings/lending-rate-summary?ccy=USDT` | current market | `ccy, avgAmt, avgRate, preRate` (plus possibly `estRate`) | [3rd] [rust-okx](https://docs.rs/crate/rust-okx/latest/source/src/api/finance/api.rs) |
| `GET /api/v5/finance/savings/lending-rate-history?ccy=USDT` | hourly history | `ccy, amt, rate, ts`; changelog adds `lendingRate` | [3rd] rust-okx; [OKX] change log |
| `POST /api/v5/finance/savings/purchase-redempt` | subscribe/redeem (authenticated; **not** to be used without approval) | — | [3rd] |

Older paths under `/api/v5/asset/...` are deprecated, per the changelog.

## 4. Rate comparison (2026-11-01 → 11-04, USDT)

| Venue | Source | Gross APR | Net to lender |
|---|---|---|---|
| Bitfinex fUST 2-day (filled trades) | our backfill, 26,967 trades | mean 6.53 %, median 6.43 % | ≈ 5.5 % after the 15 % fee, but fills are not guaranteed |
| OKX Flexible USDT | **[3rd]** hourly `rate` from `lending-rate-history`, collected by [0xmountaintop/okx-lending-rate](https://github.com/0xmountaintop/okx-lending-rate) (87 h) | median 4.00 %, mean 4.25 %, range 4.0–6.7 % | ≈ 3.4–3.6 % after 15 % **if** the published rate is gross (inference from the FAQ formula) |
| Aave V3 Polygon/Arbitrum | our on-chain read 2026-11-04 18:03 UTC | — | 3.0–3.75 % (supply APR is already net of the reserve factor) |

**Inference:**
- In this window, Bitfinex funding paid the most, and OKX Flexible and Aave were similar.
- The windows are short (3–4 days) and OKX's data is third-party, so this is a snapshot, not a conclusion.
- OKX Flexible's appeal is operational: instant redemption, no gas, and simple, hourly matching. It suits a liquidity buffer more than yield maximisation.
- Risks differ:
  - Bitfinex and OKX carry exchange custody and counterparty risk;
  - Aave carries smart-contract, oracle and bridge risk.

## 5. Proposed implementation (once `www.okx.com` is allowed)

1. **Verify** the endpoints, fields and units against `docs-v5` and live responses. In particular: is `rate` gross, an annual fraction, and per hour?
2. **Adapter** `fundpilot.exchanges.okx`: public `lending-rate-summary` / `lending-rate-history` with backfill pagination, stored in a new `cex_earn_rates` table (origin-tagged).
3. **Flexible fill model.** Reuse the auction idea: the hourly clearing APR from history, own impact ignored at first. Credit 85 % of the earnings.
4. **Venue allocator** for USDT/USDC across Bitfinex funding, OKX Flexible and Aave L2s:
   - expected net APR minus a liquidity/lock-up penalty;
   - Bitfinex credits lock for up to the offer period;
   - OKX Flexible is instant;
   - Fixed locks to maturity;
   - transfer costs between venues (withdrawal fees and on-chain gas).
5. **Fixed product decision rule.** Choose a fixed term when its APR beats the *forecast* average flexible/Bitfinex net APR over the term by a margin that pays for the lost liquidity. The forecast comes from the existing ML rate-forecast pipeline.
6. **No authenticated calls** without explicit approval (same policy as Bitfinex Phase 7).
