"use client";
import { fmtDate } from "@/lib/format";

export interface Health {
  provider_id: string; status: string; circuit?: string; success_rate: number | null; calls: number;
  p95_latency_ms: number | null; last_failure_at?: string; last_error?: string; window_hours: number;
}

export const LOOK: Record<string, { color: string; label: string }> = {
  healthy: { color: "green", label: "healthy" }, degraded: { color: "amber", label: "degraded" },
  down: { color: "red", label: "down" }, idle: { color: "", label: "no recent traffic" },
};

export function HealthView({ h }: { h?: Health }) {
  if (!h) return null;
  const look = h.circuit === "open" ? { color: "red", label: "paused · circuit open" }
    : h.circuit === "half_open" ? { color: "amber", label: "testing recovery" } : LOOK[h.status] || LOOK.idle;
  const parts: string[] = [];
  if (h.success_rate !== null) parts.push(`${(h.success_rate * 100).toFixed(h.success_rate === 1 ? 0 : 1)}% of ${h.calls} calls ok`);
  if (h.p95_latency_ms !== null) parts.push(`p95 ${Math.round(h.p95_latency_ms)} ms`);
  return (
    <>
      <div style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span className={`badge ${look.color}`}>{look.label}</span>
        <span className="muted" style={{ fontSize: 12 }}>{parts.join(" · ") || `last ${h.window_hours}h`}</span>
      </div>
      {h.last_failure_at && <div className="muted" style={{ fontSize: 12, marginTop: 2 }} title={h.last_error}>
        Last failure {fmtDate(h.last_failure_at)}{h.last_error ? ` — ${h.last_error.slice(0, 90)}` : ""}</div>}
    </>
  );
}

/** One-word status for tables: the circuit state wins over the recent success rate. */
export function healthLook(h: Pick<Health, "status" | "circuit">) {
  return h.circuit === "open" ? { color: "red", label: "paused · circuit open" }
    : h.circuit === "half_open" ? { color: "amber", label: "testing recovery" } : LOOK[h.status] || LOOK.idle;
}
