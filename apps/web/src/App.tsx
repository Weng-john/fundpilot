import { useState } from "react";
import type { Health, Overview } from "./api";
import { Badge } from "./components/ui";
import { useApi } from "./hooks";
import { YieldsPage } from "./YieldsPage";
import { AgentPage, MarketPage, OverviewPage, RiskPage, StrategyPage, type Ctx } from "./pages";

const TABS = ["Overview", "Yields", "Market", "Strategy", "Risk", "Agent"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const [tab, setTab] = useState<Tab>("Overview");
  const [ctx, setCtx] = useState<Ctx>({ symbol: "fUST", account: "", synthetic: false });
  const health = useApi<Health>("health", {}, 15_000);
  const accounts = useApi<Overview>("overview", { symbol: ctx.symbol }, 300_000).data?.accounts ?? [];
  const h = health.data;
  return (
    <div className="app">
      <header className="top">
        <h1>FundPilot</h1>
        <nav>{TABS.map((t) => <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}</nav>
        <div className="controls">
          <input value={ctx.symbol} onChange={(e) => setCtx({ ...ctx, symbol: e.target.value })} aria-label="symbol" size={6} />
          <select value={ctx.account} onChange={(e) => setCtx({ ...ctx, account: e.target.value })} aria-label="account">
            <option value="">(no account)</option>
            {accounts.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <label><input type="checkbox" checked={ctx.synthetic} onChange={(e) => setCtx({ ...ctx, synthetic: e.target.checked })} /> include synthetic</label>
        </div>
        <div className="status">
          {h ? <>
            <Badge kind="info">{h.execution_mode}</Badge>
            {h.emergency_stop && <Badge kind="crit">EMERGENCY STOP</Badge>}
            <span className="note">v{h.version}</span>
          </> : <Badge kind="warn">API offline</Badge>}
        </div>
      </header>
      {ctx.synthetic && <div className="banner">SYNTHETIC DATA INCLUDED — not market evidence.</div>}
      <main>
        {tab === "Overview" && <OverviewPage ctx={ctx} />}
        {tab === "Yields" && <YieldsPage />}
        {tab === "Market" && <MarketPage ctx={ctx} />}
        {tab === "Strategy" && <StrategyPage ctx={ctx} />}
        {tab === "Risk" && <RiskPage ctx={ctx} writeEnabled={h?.write_api_enabled ?? false} />}
        {tab === "Agent" && <AgentPage />}
      </main>
      <footer>Rates are daily fractions; APR = daily × 365 (simple). Paper and backtest figures are SIMULATED.</footer>
    </div>
  );
}
