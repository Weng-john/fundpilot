# Supervisory AI agent

The agent is **optional** and **cannot trade**. It summarises market conditions, explains
operations, reviews utilisation, compares experiment reports, writes daily/weekly reports and
proposes configuration changes for humans to review.

## Without an LLM (default)

```bash
uv run fundpilot agent report --period daily
uv run fundpilot agent report --period weekly --account paper-ladder --output reports/week.md
```

Reports are generated from deterministic analytics (`fundpilot.agents.analytics`):

| Function | Content |
|---|---|
| `market_summary` | FRR now (daily, gross APR, APR after assumed fee), 24 h range, 7-day percentile, best bid/ask and depth, 24 h trades/volume/VWAP |
| `rate_anomalies` | FRR observations ≥ 3 σ from the trailing 24 h mean |
| `utilization_review` | idle share and equity of a paper (SIMULATED) or live (REALISED) account |
| `operational_review` | ingestion failures by job, data-quality issues, alerts, critical messages |
| `experiment_summary` | best strategy per scenario from a report JSON under `experiments/outputs` only |

Market analytics read only `origin=exchange_api` rows by default.

## With an OpenAI-compatible LLM

```bash
export FUNDPILOT_LLM_BASE_URL=https://your-provider.example/v1   # any /chat/completions API
export FUNDPILOT_LLM_MODEL=your-model
export FUNDPILOT_LLM_API_KEY=...                                  # never commit this
uv run fundpilot agent report --llm
uv run fundpilot agent ask "Why was utilisation low yesterday?"
```

* `report --llm` appends a clearly labelled *AI commentary* section after the deterministic facts.
* `ask` runs a bounded tool-calling loop (default 6 rounds) over the read-only tools.

## Safety model

| Property | How |
|---|---|
| No execution access | the tool registry contains only read-only analytics and `propose_recommendation`; a test asserts no tool name relates to placing, cancelling, approving, resuming or withdrawing |
| Prompt-injection resistance | unknown tools and unexpected arguments are refused and the refusal is returned to the model as data; LLM text is never executed |
| Deterministic validation | recommendations must target an allow-listed parameter (`agents.recommendations.RULES`) with a bounded value; risk-increasing changes are flagged |
| Human in the loop | recommendations are stored as `proposed`; `fundpilot agent recommendations accept ID` only records the decision — the operator edits the config file, and changes still pass the risk manager at run time |
| Auditability | every report and question is logged in `agent_activity` |
| File access | `experiment_summary` refuses paths outside `experiments/outputs` |

```bash
uv run fundpilot agent recommendations list
uv run fundpilot agent recommendations accept 3 --by alice   # or: reject 3
```
