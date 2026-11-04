import { useMemo, useState } from "react";
import { pct, type Yields } from "./api";
import { LineChart } from "./components/LineChart";
import { Badge, Card, Empty, ErrorLine } from "./components/ui";
import { useApi } from "./hooks";

const COLORS = ["#4ea1ff", "#3ccf91", "#9b8afb", "#f5b84a", "#ff6b6b", "#5fd4e0", "#e08bd4"];

export function YieldsPage() {
  const [days, setDays] = useState(30);
  const [showBitfinex, setShowBitfinex] = useState(true);
  const { data, error } = useApi<Yields>("yields", { days, include_bitfinex: showBitfinex }, 300_000);
  const series = useMemo(
    () =>
      (data?.venues ?? [])
        .filter((v) => (data?.daily[v.key] ?? []).length > 1)
        .map((v, i) => ({
          name: v.key,
          color: v.available ? COLORS[i % COLORS.length] : "#6b7280",
          points: (data?.daily[v.key] ?? []).map((p) => ({ t: `${p.date}T12:00:00Z`, v: p.apr * 100 })),
        })),
    [data],
  );
  const venues = [...(data?.venues ?? [])].sort((a, b) => (b.median ?? -1) - (a.median ?? -1));
  return (
    <>
      <ErrorLine error={error} />
      <Card title="Net lending yield by venue" note={data?.label}>
        <div className="controls-row">
          <label>Window{" "}
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[7, 30, 90, 180].map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </label>
          <label><input type="checkbox" checked={showBitfinex} onChange={(e) => setShowBitfinex(e.target.checked)} /> show Bitfinex (reference)</label>
        </div>
        {series.length ? <LineChart series={series} precision={2} /> : <Empty text="Not enough data yet. Run `fundpilot okx backfill` / `fundpilot import-yields`." />}
        <p className="note">Daily average, % per year. Grey = reference only (not available to you).</p>
      </Card>
      <Card title="Statistics" note={data ? `best available by median (≥ ${data.min_points_for_ranking} obs): ${data.best_available_by_median ?? "—"}` : undefined}>
        {venues.length === 0 ? <Empty /> : (
          <table>
            <thead><tr><th>Venue</th><th>Asset</th><th>Median</th><th>Mean</th><th>P10–P90</th><th>Last</th><th>Obs</th><th>Basis</th></tr></thead>
            <tbody>
              {venues.map((v) => (
                <tr key={v.key} className={v.available ? "" : "muted"}>
                  <td>{v.key === data?.best_available_by_median && <Badge kind="real">best</Badge>}{v.available ? v.venue : <><Badge kind="info">reference</Badge>{v.venue}</>}</td>
                  <td>{v.asset}</td>
                  <td>{pct(v.median)}</td>
                  <td>{pct(v.mean)}</td>
                  <td>{v.p10 === undefined ? "—" : `${pct(v.p10)} – ${pct(v.p90)}`}</td>
                  <td>{pct(v.last)}</td>
                  <td>{v.n}</td>
                  <td className="note">{v.basis}{v.note ? ` — ${v.note}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="note">Recorded market rates, not realised account returns. Bitfinex uses executed loans; your own offers may not fill. Risks differ: exchange custody (OKX, Bitfinex) vs smart contract/bridge (Aave).</p>
      </Card>
    </>
  );
}
