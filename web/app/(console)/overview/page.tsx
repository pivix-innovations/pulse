"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useTq } from "@/lib/hooks";
import { Card, ChannelBadge, EmptyState } from "@/components/ui";

interface Metrics {
  notifications: Record<string, number>;
  attempts: Record<string, Record<string, number>>;
  dead_letters: number;
  avg_confirmation_seconds: number | null;
}
interface Usage {
  enforced: boolean; rate_limit_per_second: number; rate_burst: number;
  used_today: number; daily_quota: number | null;
}
const STATUSES = ["pending", "sent", "delivered", "retrying", "failed", "dead"];

export default function Overview() {
  const [hours, setHours] = useState("24");
  const usage = useTq<Usage | null>(["usage"], () => api<Usage>("/tenant/usage").catch(() => null));
  const m = useTq<Metrics>(["metrics", hours], () => api(`/metrics?window_hours=${hours}`), { refetchInterval: 30_000 });
  const u = usage.data;

  return (
    <>
      <div className="toolbar" style={{ marginBottom: 18 }}>
        <label style={{ margin: 0 }} htmlFor="window">Window</label>
        <select id="window" style={{ width: "auto" }} value={hours} onChange={e => setHours(e.target.value)}>
          <option value="1">Last hour</option><option value="6">Last 6 hours</option>
          <option value="24">Last 24 hours</option><option value="168">Last 7 days</option><option value="720">Last 30 days</option>
        </select>
        <button className="btn" onClick={() => { m.refetch(); usage.refetch(); }}>Refresh</button>
      </div>

      {u?.enforced && <UsageCard u={u} />}

      {m.isLoading && <div className="empty muted">Loading metrics…</div>}
      {m.isError && <EmptyState title="Couldn't load metrics" hint={(m.error as Error).message} />}
      {m.data && <MetricsView m={m.data} />}
    </>
  );
}

function UsageCard({ u }: { u: Usage }) {
  const pct = u.daily_quota ? Math.min(100, Math.round((u.used_today / u.daily_quota) * 100)) : null;
  return (
    <Card title="Event limits" sub={<>What this tenant may send to <code>POST /events</code></>} style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", gap: 32, flexWrap: "wrap", alignItems: "center" }}>
        <div><div className="muted" style={{ fontSize: 12 }}>Sustained rate</div><div style={{ fontWeight: 600 }}>{u.rate_limit_per_second}/s</div></div>
        <div><div className="muted" style={{ fontSize: 12 }}>Burst</div><div style={{ fontWeight: 600 }}>{u.rate_burst}</div></div>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div className="muted" style={{ fontSize: 12 }}>Today (UTC)</div>
          {u.daily_quota === null ? (
            <div style={{ fontWeight: 600 }}>{u.used_today.toLocaleString()} events · no daily quota</div>
          ) : (
            <>
              <div style={{ fontWeight: 600 }}>{u.used_today.toLocaleString()} of {u.daily_quota.toLocaleString()} events</div>
              <div style={{ height: 6, borderRadius: 3, background: "var(--surface-2)", marginTop: 6, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${pct}%`, background: pct! >= 90 ? "var(--red)" : pct! >= 70 ? "var(--amber)" : "var(--accent)" }} />
              </div>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}

function MetricsView({ m }: { m: Metrics }) {
  const total = Object.values(m.notifications).reduce((a, b) => a + b, 0);
  const totals: Record<string, number> = {};
  for (const per of Object.values(m.attempts)) for (const [s, n] of Object.entries(per)) totals[s] = (totals[s] || 0) + n;
  const all = Object.values(totals).reduce((a, b) => a + b, 0);
  const channels = Object.keys(m.attempts);
  return (
    <>
      <div className="stat-grid">
        <div className="stat"><div className="stat-label">Notifications</div><div className="stat-value">{total}</div>
          <div className="stat-hint">{Object.entries(m.notifications).map(([k, v]) => `${v} ${k}`).join(" · ") || "none in window"}</div></div>
        <div className="stat"><div className="stat-label">Delivery attempts</div><div className="stat-value">{all}</div>
          <div className="stat-hint">{totals.delivered || 0} confirmed delivered</div></div>
        <div className="stat"><div className="stat-label">Dead letters</div>
          <div className="stat-value" style={m.dead_letters ? { color: "var(--red)" } : undefined}>{m.dead_letters}</div>
          <div className="stat-hint">escalation exhausted</div></div>
        <div className="stat"><div className="stat-label">Avg confirmation</div>
          <div className="stat-value">{m.avg_confirmation_seconds == null ? "—" : m.avg_confirmation_seconds.toFixed(1) + "s"}</div>
          <div className="stat-hint">sent → delivered</div></div>
      </div>
      <Card title="Attempts by channel" sub="Status breakdown per delivery channel over the selected window" flush>
        {channels.length === 0 ? <EmptyState title="No delivery attempts" hint="Send an event to see channel activity here." /> : (
          <table className="matrix">
            <thead><tr><th>Channel</th>{STATUSES.map(s => <th key={s} style={{ textAlign: "right" }}>{s}</th>)}<th style={{ textAlign: "right" }}>Total</th></tr></thead>
            <tbody>{channels.map(ch => {
              const row = m.attempts[ch];
              return (
                <tr key={ch}><td><ChannelBadge channel={ch} /></td>
                  {STATUSES.map(s => <td key={s} className={`num ${row[s] ? "" : "zero"}`}>{row[s] || 0}</td>)}
                  <td className="num" style={{ fontWeight: 600 }}>{Object.values(row).reduce((a, b) => a + b, 0)}</td></tr>
              );
            })}</tbody>
          </table>
        )}
      </Card>
    </>
  );
}
