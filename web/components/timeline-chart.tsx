"use client";
import { useMemo } from "react";

export interface TimelinePoint { bucket: string; counts: Record<string, number> }

// Three series keep the chart legible: what finished well, what is still moving, what went wrong.
const SERIES = [
  { key: "delivered", label: "Delivered", color: "var(--green)", statuses: ["delivered"] },
  { key: "inflight", label: "In flight", color: "var(--blue)", statuses: ["pending", "sent", "retrying"] },
  { key: "failed", label: "Failed", color: "var(--red)", statuses: ["failed", "dead"] },
] as const;

function niceMax(n: number) {
  if (n <= 4) return 4;
  const p = 10 ** Math.floor(Math.log10(n));
  return Math.ceil(n / p) * p;
}

/** Attempts per time bucket, stacked by outcome. Pure SVG: bars share a baseline, so heights compare honestly. */
export function TimelineChart({ points, bucketSeconds, windowHours, asOf }: { points: TimelinePoint[]; bucketSeconds: number; windowHours: number; asOf: number }) {
  const bars = useMemo(() => {
    const step = bucketSeconds * 1000;
    const end = Math.floor(asOf / step) * step;   // the moment the data was fetched, so rendering stays pure
    const n = Math.max(1, Math.round((windowHours * 3600) / bucketSeconds));
    const byStart = new Map(points.map(p => [Math.floor(new Date(p.bucket).getTime() / step) * step, p.counts]));
    return Array.from({ length: n }, (_, i) => {
      const t = end - (n - 1 - i) * step;
      const c = byStart.get(t) ?? {};
      const vals = SERIES.map(s => s.statuses.reduce((a, st) => a + (c[st] || 0), 0));
      return { t, vals, total: vals.reduce((a, b) => a + b, 0) };
    });
  }, [points, bucketSeconds, windowHours, asOf]);

  const max = niceMax(Math.max(0, ...bars.map(b => b.total)));
  const W = 720, H = 180, L = 36, B = 22, T = 8;
  const plotW = W - L - 4, plotH = H - B - T;
  const bw = plotW / bars.length;
  const fmtT = (t: number) => bucketSeconds >= 86400
    ? new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const everyN = Math.ceil(bars.length / 6);

  return (
    <div>
      <div style={{ display: "flex", gap: 16, marginBottom: 8, flexWrap: "wrap", fontSize: 12 }}>
        {SERIES.map(s => <span key={s.key} className="muted" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: s.color }} />{s.label}</span>)}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Delivery attempts over time, by outcome" style={{ width: "100%", height: "auto", display: "block" }}>
        {[0, 0.5, 1].map(f => (
          <g key={f}>
            <line x1={L} x2={W - 4} y1={T + plotH * (1 - f)} y2={T + plotH * (1 - f)} stroke="var(--border)" strokeWidth={1} />
            <text x={L - 6} y={T + plotH * (1 - f) + 4} textAnchor="end" fontSize={10} fill="var(--faint)">{Math.round(max * f)}</text>
          </g>
        ))}
        {bars.map((b, i) => {
          let y = T + plotH;
          const x = L + i * bw;
          return (
            <g key={b.t}>
              <title>{`${fmtT(b.t)} · ${b.total} attempt${b.total === 1 ? "" : "s"}${SERIES.map((s, k) => b.vals[k] ? ` · ${b.vals[k]} ${s.label.toLowerCase()}` : "").join("")}`}</title>
              <rect x={x} y={T} width={bw} height={plotH} fill="transparent" />
              {SERIES.map((s, k) => {
                const h = (b.vals[k] / max) * plotH;
                y -= h;
                return h > 0 ? <rect key={s.key} x={x + bw * 0.12} y={y} width={Math.max(1, bw * 0.76)} height={h} fill={s.color} rx={1} /> : null;
              })}
              {i % everyN === 0 && <text x={x + bw / 2} y={H - 6} textAnchor="middle" fontSize={10} fill="var(--faint)">{fmtT(b.t)}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
