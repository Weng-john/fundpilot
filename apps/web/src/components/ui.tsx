import type { ReactNode } from "react";

export function Card({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <section className="card">
      <header>
        <h3>{title}</h3>
        {note && <span className="note">{note}</span>}
      </header>
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Badge({ kind, children }: { kind: "sim" | "real" | "warn" | "crit" | "info"; children: ReactNode }) {
  return <span className={`badge badge-${kind}`}>{children}</span>;
}

export function Bars({ rows, format }: { rows: { label: string; value: number; tone?: string }[]; format: (v: number) => string }) {
  const max = Math.max(1e-12, ...rows.map((r) => r.value));
  if (rows.length === 0) return <Empty />;
  return (
    <div className="bars">
      {rows.map((r) => (
        <div className="bar-row" key={r.label}>
          <span className="bar-label">{r.label}</span>
          <span className="bar-track">
            <span className={`bar-fill ${r.tone ?? ""}`} style={{ width: `${(r.value / max) * 100}%` }} />
          </span>
          <span className="bar-value">{format(r.value)}</span>
        </div>
      ))}
    </div>
  );
}

export function Empty({ text = "No data yet." }: { text?: string }) {
  return <p className="empty">{text}</p>;
}

export function ErrorLine({ error }: { error: string | null }) {
  return error ? <p className="error">API error: {error}</p> : null;
}
