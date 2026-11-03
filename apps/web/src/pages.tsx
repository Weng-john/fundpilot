import { useMemo, useState } from "react";
import {
  emergencyStop,
  num,
  pct,
  rate,
  type Agent,
  type Book,
  type Execution,
  type ExperimentItem,
  type FrrSeries,
  type ModelItem,
  type Overview,
  type RateBin,
  type Risk,
  type StatsSeries,
} from "./api";
import { LineChart } from "./components/LineChart";
import { Badge, Bars, Card, Empty, ErrorLine, Stat } from "./components/ui";
import { useApi } from "./hooks";

const MATURITY_ORDER = ["<1d", "1-2d", "2-7d", "7-30d", ">30d"];

export type Ctx = { symbol: string; account: string; synthetic: boolean };

export function OverviewPage({ ctx }: { ctx: Ctx }) {
  const { data, error } = useApi<Overview>("overview", { symbol: ctx.symbol, account: ctx.account, include_synthetic: ctx.synthetic });
  const m = data?.market;
  const a = data?.account;
  return (
    <>
      <ErrorLine error={error} />
      <Card title={`Market — ${ctx.symbol}`} note={ctx.synthetic ? "includes SYNTHETIC data" : "recorded exchange observations"}>
        <div className="stats">
          <Stat label="FRR" value={rate(m?.frr_daily)} hint={`APR ${pct(m?.frr_apr)} gross`} />
          <Stat label="FRR APR after fee" value={pct(m?.frr_net_apr_after_fee)} hint="simple, assumed 15% fee" />
          <Stat label="24h range" value={`${rate(m?.frr_24h_min)} – ${rate(m?.frr_24h_max)}`} />
          <Stat label="7d percentile" value={pct(m?.frr_7d_percentile, 0)} />
          <Stat label="Trades 24h" value={num(m?.trades_24h, 0)} hint={`volume ${num(m?.volume_24h, 0)}`} />
          <Stat label="Last ticker" value={m?.last_ticker_at ? new Date(m.last_ticker_at).toLocaleString() : "—"} />
        </div>
      </Card>
      <Card title={a ? `Account ${a.account}` : "Account"} note={a?.label}>
        {!a ? (
          <Empty text={ctx.account ? "Account not found." : "Select a paper account above."} />
        ) : (
          <>
            <p><Badge kind="sim">SIMULATED</Badge> Paper-trading figures: simulated fills on live data, not realised returns.</p>
            <div className="stats">
              <Stat label="Total capital" value={num(a.total)} />
              <Stat label="Available" value={num(a.available)} />
              <Stat label="Lent (active credits)" value={num(a.lent)} hint={`${a.active_credits} credits`} />
              <Stat label="Unfilled offers" value={num(a.offered)} hint={`${a.open_offers} offers`} />
              <Stat label="Utilization" value={pct(a.utilization)} />
              <Stat label="Interest (gross / fees)" value={`${num(a.gross_interest, 4)} / ${num(a.fees, 4)}`} />
              <Stat label="Weighted credit APR" value={pct(a.weighted_credit_apr)} hint="simple, gross" />
            </div>
          </>
        )}
      </Card>
    </>
  );
}

export function MarketPage({ ctx }: { ctx: Ctx }) {
  const q = { symbol: ctx.symbol, include_synthetic: ctx.synthetic };
  const frr = useApi<FrrSeries>("market/frr", { ...q, hours: 168 });
  const book = useApi<Book>("market/book", q);
  const dist = useApi<{ bins: RateBin[] }>("market/rate-distribution", { ...q, hours: 24 });
  const stats = useApi<StatsSeries>("market/stats", { ...q, days: 30 });
  const frrSeries = useMemo(
    () => [{ name: "FRR %/day", color: "#4ea1ff", points: (frr.data?.points ?? []).map((p) => ({ t: p.t, v: Number(p.frr) * 100 })) }],
    [frr.data],
  );
  const statSeries = useMemo(
    () => [
      { name: "provided", color: "#9b8afb", points: (stats.data?.points ?? []).filter((p) => p.funding_amount).map((p) => ({ t: p.t, v: Number(p.funding_amount) })) },
      { name: "used", color: "#3ccf91", points: (stats.data?.points ?? []).filter((p) => p.funding_amount_used).map((p) => ({ t: p.t, v: Number(p.funding_amount_used) })) },
    ],
    [stats.data],
  );
  const levels = book.data?.levels ?? [];
  return (
    <>
      <Card title="FRR — last 7 days" note="% per day">
        <ErrorLine error={frr.error} />
        {frrSeries[0].points.length ? <LineChart series={frrSeries} /> : <Empty />}
      </Card>
      <div className="grid2">
        <Card title="Order book depth" note={book.data?.captured_at ? new Date(book.data.captured_at).toLocaleString() : undefined}>
          <h4>Borrower bids (demand)</h4>
          <Bars rows={levels.filter((l) => l.side === "bid").slice(-12).reverse().map((l) => ({ label: `${rate(l.rate)} ${l.period_days}d`, value: Number(l.amount), tone: "bid" }))} format={(v) => num(v, 0)} />
          <h4>Lender offers (supply)</h4>
          <Bars rows={levels.filter((l) => l.side === "ask").slice(0, 12).map((l) => ({ label: `${rate(l.rate)} ${l.period_days}d`, value: Number(l.amount), tone: "ask" }))} format={(v) => num(v, 0)} />
        </Card>
        <Card title="Traded rate distribution — 24h" note="volume by rate">
          <Bars rows={(dist.data?.bins ?? []).filter((b) => b.count).map((b) => ({ label: rate(b.low), value: b.volume }))} format={(v) => num(v, 0)} />
        </Card>
      </div>
      <Card title="Funding supply and usage — 30 days" note="funding stats">
        {statSeries[0].points.length ? <LineChart series={statSeries} precision={0} /> : <Empty />}
      </Card>
    </>
  );
}

export function StrategyPage({ ctx }: { ctx: Ctx }) {
  const exps = useApi<{ experiments: ExperimentItem[] }>("experiments");
  const models = useApi<{ models: ModelItem[] }>("models");
  const exec = useApi<Execution>("execution", { account: ctx.account || undefined });
  return (
    <>
      <Card title="Backtests, ML and RL experiments" note="SIMULATED / out-of-sample">
        <ErrorLine error={exps.error} />
        {(exps.data?.experiments ?? []).length === 0 ? (
          <Empty text="No reports yet. Run `fundpilot backtest`, `fundpilot ml` or `fundpilot rl`." />
        ) : (
          <table>
            <thead><tr><th>Report</th><th>Data</th><th>Best by scenario (net ann. yield, excess vs baseline)</th></tr></thead>
            <tbody>
              {exps.data!.experiments.map((e) => (
                <tr key={e.path}>
                  <td><code>{e.path}</code></td>
                  <td>{e.data_label === "SYNTHETIC" ? <Badge kind="warn">SYNTHETIC</Badge> : <Badge kind="sim">SIMULATED</Badge>}</td>
                  <td>
                    {e.best_by_scenario
                      ? Object.entries(e.best_by_scenario).map(([s, b]) => (
                          <div key={s}>{s}: <b>{b.strategy}</b> {pct(b.net_annualized_yield, 3)} ({b.excess_vs_baseline === null ? "baseline" : pct(b.excess_vs_baseline, 3)})</div>
                        ))
                      : e.task ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Models" note="FORECAST — metrics are out-of-sample walk-forward">
        {(models.data?.models ?? []).length === 0 ? <Empty text="No registered models." /> : (
          <table>
            <thead><tr><th>Task</th><th>Version</th><th>Labels</th><th>Training window</th><th>Out-of-sample</th></tr></thead>
            <tbody>
              {models.data!.models.map((m) => (
                <tr key={m.version}>
                  <td>{m.task}</td><td><code>{m.version}</code></td>
                  <td>{m.label_source ? <Badge kind={m.label_source === "observed" ? "real" : "warn"}>{m.label_source}</Badge> : "—"}</td>
                  <td>{m.training_window?.map((t) => t.slice(0, 16)).join(" → ")}</td>
                  <td>{Object.entries(m.evaluation).map(([n, v]) => (
                    <div key={n}>{n}: {Object.entries(v).filter(([k]) => ["mae", "skill_vs_persistence", "brier", "auc", "ece"].includes(k)).map(([k, x]) => `${k}=${typeof x === "number" ? x.toPrecision(3) : String(x)}`).join(" ")}</div>
                  ))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Execution statistics" note={ctx.account || "all accounts"}>
        <div className="stats">
          {Object.entries(exec.data?.counts ?? {}).map(([k, v]) => <Stat key={k} label={k} value={num(v, 0)} />)}
        </div>
        {(exec.data?.recent ?? []).length ? (
          <table>
            <thead><tr><th>Time</th><th>Mode</th><th>Action</th><th>Outcome</th><th>Detail</th></tr></thead>
            <tbody>{exec.data!.recent.slice(0, 20).map((r, i) => <tr key={i}><td>{new Date(r.at).toLocaleString()}</td><td>{r.mode}</td><td>{r.action}</td><td>{r.outcome}</td><td>{r.detail ?? ""}</td></tr>)}</tbody>
          </table>
        ) : <Empty />}
      </Card>
    </>
  );
}

export function RiskPage({ ctx, writeEnabled }: { ctx: Ctx; writeEnabled: boolean }) {
  const { data, error } = useApi<Risk>("risk", { account: ctx.account || undefined }, 15_000);
  const [token, setToken] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const a = data?.account;
  return (
    <>
      <ErrorLine error={error} />
      <Card title="Emergency controls">
        <p>
          Status: {data?.emergency_stop ? <Badge kind="crit">STOPPED — new offers suspended</Badge> : <Badge kind="info">normal</Badge>}
          {data?.emergency_reason && <> — {data.emergency_reason}</>}
        </p>
        <p className="warn-text">{data?.credits_note}</p>
        {writeEnabled ? (
          <form className="stop-form" onSubmit={(e) => {
            e.preventDefault();
            emergencyStop(reason, token).then(setMsg).catch((err: unknown) => setMsg(String(err)));
          }}>
            <input placeholder="reason" value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} />
            <input placeholder="API token" type="password" value={token} onChange={(e) => setToken(e.target.value)} required />
            <button type="submit" className="danger">Emergency stop</button>
          </form>
        ) : <p className="note">Emergency stop from the dashboard is disabled (no FUNDPILOT_API_TOKEN). Use <code>fundpilot emergency-stop</code>.</p>}
        {msg && <p>{msg}</p>}
        <p className="note">Resuming is only possible from the CLI: <code>fundpilot resume --confirm</code>.</p>
      </Card>
      <div className="grid2">
        <Card title="Locked capital — maturity" note={a?.label}>
          <Bars rows={MATURITY_ORDER.filter((k) => a?.maturity_distribution[k]).map((k) => ({ label: k, value: Number(a!.maturity_distribution[k]) }))} format={(v) => num(v)} />
        </Card>
        <Card title="Exposure by period" note={a?.label}>
          <Bars rows={Object.entries(a?.exposure_by_period ?? {}).sort(([x], [y]) => parseInt(x) - parseInt(y)).map(([k, v]) => ({ label: k, value: Number(v) }))} format={(v) => num(v)} />
        </Card>
      </div>
      <Card title="Pending manual approvals">
        {(data?.pending_approvals ?? []).length === 0 ? <Empty text="None." /> : (
          <table><thead><tr><th>ID</th><th>Account</th><th>Offer</th><th>Expires</th></tr></thead>
            <tbody>{data!.pending_approvals.map((p) => <tr key={p.id}><td>{p.id}</td><td>{p.account}</td><td><code>{JSON.stringify(p.payload)}</code></td><td>{new Date(p.expires_at).toLocaleString()}</td></tr>)}</tbody></table>
        )}
        <p className="note">Approve with <code>fundpilot approvals approve ID</code>.</p>
      </Card>
      <Card title="Operational alerts">
        {(data?.alerts ?? []).length === 0 ? <Empty text="No alerts." /> : (
          <table><thead><tr><th>Time</th><th>Severity</th><th>Code</th><th>Message</th></tr></thead>
            <tbody>{data!.alerts.map((al, i) => <tr key={i}><td>{new Date(al.at).toLocaleString()}</td><td><Badge kind={al.severity === "critical" ? "crit" : al.severity === "warning" ? "warn" : "info"}>{al.severity}</Badge></td><td>{al.code}</td><td>{al.message}</td></tr>)}</tbody></table>
        )}
      </Card>
    </>
  );
}

export function AgentPage() {
  const { data, error } = useApi<Agent>("agent");
  return (
    <>
      <ErrorLine error={error} />
      <Card title="Recommendations" note="validated proposals — never applied automatically">
        {(data?.recommendations ?? []).length === 0 ? <Empty text="No recommendations." /> : (
          <table><thead><tr><th>ID</th><th>Parameter</th><th>Value</th><th>Risk</th><th>Status</th><th>Rationale</th></tr></thead>
            <tbody>{data!.recommendations.map((r) => <tr key={r.id}><td>{r.id}</td><td><code>{r.parameter}</code></td><td>{r.value}</td><td>{r.risk_increasing ? <Badge kind="warn">increases risk</Badge> : <Badge kind="info">reduces/neutral</Badge>}</td><td>{r.status}</td><td>{r.rationale}</td></tr>)}</tbody></table>
        )}
        <p className="note">Review with <code>fundpilot agent recommendations accept|reject ID</code>, then edit the config yourself.</p>
      </Card>
      <Card title="Activity log">
        {(data?.activity ?? []).length === 0 ? <Empty text="No agent activity. Try `fundpilot agent report`." /> : (
          <table><thead><tr><th>Time</th><th>Kind</th><th>Summary</th></tr></thead>
            <tbody>{data!.activity.map((a, i) => <tr key={i}><td>{new Date(a.at).toLocaleString()}</td><td>{a.kind}</td><td>{a.summary}</td></tr>)}</tbody></table>
        )}
      </Card>
    </>
  );
}
