// Typed access to the FundPilot dashboard API. All rates are daily fractions.

export type Health = {
  status: string;
  version: string;
  execution_mode: string;
  emergency_stop: boolean;
  emergency_reason: string | null;
  write_api_enabled: boolean;
};

export type MarketSummary = {
  symbol: string;
  as_of: string;
  data_origin: string;
  last_ticker_at: string | null;
  frr_daily: number | null;
  frr_apr: number | null;
  frr_net_apr_after_fee: number | null;
  frr_24h_mean: number | null;
  frr_24h_min: number | null;
  frr_24h_max: number | null;
  frr_7d_percentile: number | null;
  best_bid: number | null;
  best_ask: number | null;
  ask_depth: number | null;
  bid_depth: number | null;
  trades_24h: number;
  volume_24h: number;
  vwap_24h: number | null;
};

export type PaperAccount = {
  account: string;
  label: string;
  updated_at: string;
  total: string;
  available: string;
  offered: string;
  lent: string;
  utilization: string | null;
  active_credits: number;
  open_offers: number;
  gross_interest: string;
  fees: string;
  paid_interest: string;
  pending_interest: string;
  weighted_credit_rate_daily: string | null;
  weighted_credit_apr: string | null;
  maturity_distribution: Record<string, string>;
  exposure_by_period: Record<string, string>;
};

export type Overview = { market: MarketSummary; accounts: string[]; account?: PaperAccount | null };
export type FrrSeries = { symbol: string; points: { t: string; frr: string }[] };
export type BookLevel = { side: "bid" | "ask"; rate: string; period_days: number; amount: string };
export type Book = { symbol: string; captured_at: string | null; origin?: string; levels: BookLevel[] };
export type RateBin = { low: number; high: number; volume: number; count: number };
export type StatsSeries = {
  points: { t: string; funding_amount: string | null; funding_amount_used: string | null }[];
};
export type Alert = { at: string; code: string; severity: string; message: string; source: string };
export type Risk = {
  emergency_stop: boolean;
  emergency_reason: string | null;
  credits_note: string;
  alerts: Alert[];
  pending_approvals: { id: number; account: string; payload: Record<string, unknown>; expires_at: string }[];
  account?: PaperAccount | null;
};
export type Execution = {
  counts: Record<string, number>;
  recent: { at: string; mode: string; action: string; outcome: string; detail: string | null }[];
};
export type ExperimentItem = {
  path: string;
  kind?: string;
  data_label?: string;
  warning?: string;
  experiment?: string;
  task?: string;
  best_by_scenario?: Record<string, { strategy: string; net_annualized_yield: number; excess_vs_baseline: string | null }>;
  models?: Record<string, Record<string, unknown>>;
};
export type ModelItem = {
  task: string;
  version: string;
  model_type: string;
  label_source: string | null;
  training_window: [string, string];
  evaluation: Record<string, Record<string, unknown>>;
  top_features: string[];
};
export type Agent = {
  recommendations: { id: number; parameter: string; value: string; rationale: string; risk_increasing: boolean; status: string; source: string; created_at: string }[];
  activity: { at: string; kind: string; summary: string }[];
};

export type Query = Record<string, string | number | boolean | undefined>;

export async function get<T>(path: string, query: Query = {}): Promise<T> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== "") params.set(k, String(v));
  const qs = params.toString();
  const res = await fetch(`/api/${path}${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export async function emergencyStop(reason: string, token: string): Promise<string> {
  const res = await fetch("/api/controls/emergency-stop", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ reason, by: "dashboard" }),
  });
  const body = (await res.json()) as { message?: string; detail?: string };
  if (!res.ok) throw new Error(body.detail ?? res.statusText);
  return body.message ?? "emergency stop activated";
}

// -- formatting -------------------------------------------------------------
export const pct = (x: number | string | null | undefined, digits = 2): string =>
  x === null || x === undefined || x === "" ? "—" : `${(Number(x) * 100).toFixed(digits)}%`;
export const num = (x: number | string | null | undefined, digits = 2): string =>
  x === null || x === undefined || x === ""
    ? "—"
    : Number(x).toLocaleString(undefined, { maximumFractionDigits: digits });
export const rate = (x: number | string | null | undefined): string =>
  x === null || x === undefined || x === "" ? "—" : `${(Number(x) * 100).toFixed(4)}%/d`;

export type YieldVenue = {
  key: string;
  venue: string;
  asset: string;
  basis: string;
  available: boolean;
  note: string | null;
  n: number;
  mean?: number;
  median?: number;
  p10?: number;
  p90?: number;
  last?: number;
  last_at?: string;
};
export type Yields = {
  unit: string;
  label: string;
  venues: YieldVenue[];
  best_available_by_median: string | null;
  min_points_for_ranking: number;
  daily: Record<string, { date: string; apr: number }[]>;
};
