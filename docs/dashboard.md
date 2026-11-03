# Dashboard

A React + TypeScript + Vite single-page app (`apps/web`) on top of a FastAPI backend
(`fundpilot.api`). Designed for daily monitoring; data refreshes every 15-60 s.

```bash
uv sync --extra api                       # fastapi + uvicorn (also in the dev group)
uv run fundpilot serve                    # API on http://127.0.0.1:8000
cd apps/web && npm ci && npm run dev      # UI on http://127.0.0.1:5173 (proxies /api)
```

Production build: `npm run build` produces static files in `apps/web/dist`; serve them behind any
web server that proxies `/api` to `fundpilot serve`.

## Pages

| Tab | Content |
|---|---|
| Overview | FRR (daily, APR gross and after the assumed fee), 24 h range, 7-day percentile, trade activity; selected paper account: total/available/lent/unfilled capital, utilisation, interest, weighted credit APR |
| Market | FRR history (7 d), order-book depth (bids = borrower demand, asks = lender supply), traded-rate distribution (24 h), funding supply/usage from funding stats |
| Strategy | backtest/ML/RL reports with best strategy per scenario and excess vs. baseline, registered models with out-of-sample metrics and label source, execution statistics and audit trail |
| Risk | emergency status and stop button, locked capital by maturity, exposure by period, pending approvals, operational alerts |
| Agent | recommendations (validated, never auto-applied) and the agent activity log |

## Labelling rules

* Paper and backtest figures carry **SIMULATED**; live figures will carry **REALISED**; model
  outputs **FORECAST**.
* Market views read only genuine exchange observations unless *include synthetic* is ticked, which
  shows a permanent **SYNTHETIC DATA** banner.

## Write access

The API is read-only except `POST /api/controls/emergency-stop`, which only *reduces* risk. It is
disabled unless `FUNDPILOT_API_TOKEN` is set and requires `Authorization: Bearer <token>`.
There is no resume endpoint: resuming requires `fundpilot resume --confirm`. The API binds to
`127.0.0.1` by default; put it behind authentication (VPN or an authenticating reverse proxy)
before exposing it.
