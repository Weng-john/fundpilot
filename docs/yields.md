# Cross-venue yield comparison and automatic collection

Focus venues (2026-11-04):
- **OKX Simple Earn Flexible** (USDT, USDC).
- **Aave V3 on Polygon and Arbitrum**.
- **Bitfinex** is kept as a *reference only*, because it currently does not accept new Taiwan users. Its code and data are unchanged and can be re-enabled later.

## Automatic collection (no Claude tokens)

`.github/workflows/collect-yields.yml` runs **hourly on GitHub's servers** and appends public data to CSV files on the **`collected-data`** branch. It uses no exchange credentials and no Claude session. Each run:

- fetches OKX Flexible `lending-rate-history` for the last 48 h (overlaps are deduplicated);
- reads one Aave V3 snapshot per reserve on Polygon and Arbitrum via public publicnode RPCs.

Notes:
- Scheduled workflows run only from the **default branch**, so collection starts after this branch is merged into `main`. It can also be started manually: Actions → *Collect yields* → *Run workflow*.
- GitHub Actions minutes: about 1 minute per run, roughly 720 minutes/month. That fits the free tier of private repositories (2,000 min/month, per GitHub's published plan, which may change).
- If OKX blocks GitHub's runner IPs, the job fails visibly in the Actions tab. In that case run the collector on your own machine instead (below).

Load the collected data into a database for the dashboard:

```bash
git fetch origin collected-data
git worktree add ../fundpilot-data origin/collected-data
uv run fundpilot import-yields --csv-dir ../fundpilot-data/yields
```

Or collect locally, for example from cron:

```bash
FUNDPILOT_AAVE_RPC_URLS='{"polygon": "https://polygon-bor-rpc.publicnode.com", "arbitrum": "https://arbitrum-one-rpc.publicnode.com"}' \
  uv run fundpilot collect-yields --csv-dir data/yields
```

## Comparison

- `uv run fundpilot compare-yields --days 30` prints a table.
- The dashboard has a **Yields** tab (`/api/yields`) with a daily chart and statistics.
- Only venues with at least 24 observations are eligible for "best".
- Bitfinex is grey and marked *reference*.

All figures are recorded market rates, not realised account returns.
