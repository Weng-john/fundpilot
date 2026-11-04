# Research: OKX Simple Earn (Flexible and Fixed) for USDT/USDC

Status: **research only, no code yet.** Compiled 2026-11-04. `www.okx.com`, including the API and
the `docs-v5` reference, is **not reachable** from the development environment. Everything below
comes from web-search summaries of OKX help pages, third-party SDKs and a third-party data
collector.

Labels: **[OKX]** = OKX help or announcement page (via search summary) ·
**[3rd]** = third-party source · **[inference]** = my reasoning.

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
