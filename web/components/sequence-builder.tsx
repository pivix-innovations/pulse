"use client";
import { chLabel } from "@/lib/format";
import type { Provider } from "@/lib/types";
import { ChannelBadge } from "./ui";

export const SEQ_CHANNELS = ["email", "push", "sms", "in_app"] as const;
const FORMATS = ["markdown", "html", "text"];
const STATUSES: [string, string][] = [
  ["", "no condition on a previous step"], ["not_delivered", "was not delivered (unread)"], ["delivered", "was delivered (read)"],
  ["failed", "failed"], ["skipped", "was skipped"],
];
const OPS: [string, string][] = [["eq", "is"], ["ne", "is not"], ["in", "is one of"], ["not_in", "is none of"], ["exists", "exists"], ["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"], ["contains", "contains"]];
export const MAX_STEPS = 10;

export interface Check { field: string; op: string; value: string }
export interface SeqStep {
  id: string; channel: string; subject: string; body: string; format: string; template: string | null;
  delay: string; condStep: string; condStatus: string; checks: Check[];
  raw: Record<string, unknown>;   // keys the builder does not edit survive a round trip
}
type Tpl = { name: string; channel: string; is_active: boolean; published_version: number | null };

const parseValue = (op: string, v: string): unknown => {
  if (op === "exists") return true;
  if (op === "in" || op === "not_in") return v.split(",").map(x => x.trim()).filter(Boolean).map(x => (x !== "" && !isNaN(Number(x)) ? Number(x) : x));
  if (v !== "" && !isNaN(Number(v))) return Number(v);
  if (v === "true") return true;
  if (v === "false") return false;
  return v;
};
const showValue = (v: unknown) => (Array.isArray(v) ? v.join(", ") : v === undefined || v === true ? "" : String(v));

export function fromSteps(steps: Record<string, any>[]): SeqStep[] {
  return steps.map((s, i) => {
    const w = s.when ?? {};
    const status = w.status === "unread" ? "not_delivered" : w.status === "read" ? "delivered" : (w.status ?? "");
    return {
      id: s.id ?? `step-${i + 1}`, channel: s.channel, subject: s.subject ?? "", body: s.body ?? "", format: s.format ?? "text",
      template: s.template ?? null, delay: s.delay !== undefined ? String(s.delay) : s.delay_seconds ? String(s.delay_seconds) : "",
      condStep: w.step ?? "", condStatus: status,
      checks: (w.payload ?? []).map((c: Record<string, unknown>) => ({ field: String(c.field ?? ""), op: String(c.op ?? "eq"), value: showValue(c.value) })),
      raw: s,
    };
  });
}

export function toSteps(steps: SeqStep[]): Record<string, unknown>[] {
  return steps.map(s => {
    const { delay: _d, delay_seconds: _ds, when: _w, subject: _s, body: _b, format: _f, template: _t, id: _i, channel: _c, ...keep } = s.raw as Record<string, unknown>;
    void _d; void _ds; void _w; void _s; void _b; void _f; void _t; void _i; void _c;
    const out: Record<string, unknown> = { ...keep, id: s.id, channel: s.channel };
    if (s.template) out.template = s.template;
    else { out.subject = s.subject || null; out.body = s.body; out.format = s.format; }
    if (s.delay.trim()) out.delay = /^\d+$/.test(s.delay.trim()) ? Number(s.delay.trim()) : s.delay.trim();
    const when: Record<string, unknown> = {};
    if (s.condStatus) { when.status = s.condStatus; if (s.condStep) when.step = s.condStep; }
    const checks = s.checks.filter(c => c.field.trim()).map(c => ({ field: c.field.trim(), op: c.op, ...(c.op === "exists" ? {} : { value: parseValue(c.op, c.value) }) }));
    if (checks.length) when.payload = checks;
    if (Object.keys(when).length) out.when = when;
    return out;
  });
}

const slug = (channel: string, taken: Set<string>) => {
  let id = channel.replace("_", "-");
  for (let n = 2; taken.has(id); n++) id = `${channel.replace("_", "-")}-${n}`;
  return id;
};

export function SequenceBuilder({ steps, onChange, templates, providers }: {
  steps: SeqStep[]; onChange: (s: SeqStep[]) => void; templates: Tpl[]; providers?: Provider[];
}) {
  void providers;
  const used = new Set(steps.map(s => s.channel));
  const free = SEQ_CHANNELS.filter(c => !used.has(c));
  const set = (i: number, patch: Partial<SeqStep>) => onChange(steps.map((s, k) => (k === i ? { ...s, ...patch } : s)));
  const move = (i: number, j: number) => { const a = [...steps]; [a[i], a[j]] = [a[j], a[i]]; onChange(a); };

  return (
    <div>
      <div className="muted" style={{ fontSize: 12, marginBottom: 12 }}>
        Each step sends on a channel — right away, or a while after the step before it, and only if its condition holds. One step per channel, up to {MAX_STEPS}.
        Use <code>{"{{ recipient.external_ref }}"}</code> and event fields in messages.
      </div>
      {steps.map((s, i) => {
        const earlier = steps.slice(0, i);
        const names = [...new Set(templates.filter(t => t.channel === s.channel && t.is_active && t.published_version).map(t => t.name))];
        const tplOptions = s.template && !names.includes(s.template) ? [...names, s.template] : names;
        const prev = earlier[i - 1];
        return (
          <div key={s.raw.id as string ?? i} style={{ display: "flex", gap: 12 }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 24, flex: "none" }}>
              <span className="chain-item" style={{ width: 24, height: 24, padding: 0, justifyContent: "center", borderRadius: "50%", margin: 0, background: "var(--accent)", color: "#fff", fontSize: 12 }}>{i + 1}</span>
              {i < steps.length - 1 && <span style={{ flex: 1, width: 2, background: "var(--border)", margin: "4px 0" }} />}
            </div>
            <div style={{ flex: 1, minWidth: 0, paddingBottom: 14 }}>
              {i > 0 && (
                <div className="chain-note" style={{ margin: "0 0 8px", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span>Wait</span>
                  <input value={s.delay} onChange={e => set(i, { delay: e.target.value })} placeholder="0" aria-label={`Delay before step ${i + 1}`} style={{ width: 80 }} title='Seconds, or 30s / 10m / 2h / 1d' />
                  <span>after step {i}, then send if</span>
                  <select value={s.condStatus} onChange={e => set(i, { condStatus: e.target.value })} aria-label={`Condition for step ${i + 1}`} style={{ width: "auto" }}>
                    {STATUSES.map(([v, l]) => <option key={v} value={v}>{v ? (s.condStep && s.condStep !== prev?.id ? "that step " : "the previous step ") + l : l}</option>)}
                  </select>
                  {s.condStatus && earlier.length > 1 && (
                    <select value={s.condStep} onChange={e => set(i, { condStep: e.target.value })} aria-label="Which step" style={{ width: "auto" }}>
                      <option value="">previous step</option>{earlier.map(e => <option key={e.id} value={e.id}>step {steps.indexOf(e) + 1} ({chLabel(e.channel)})</option>)}
                    </select>)}
                </div>)}
              <div className="chain" style={{ marginBottom: 0 }}>
                <div className="chain-head">
                  <span><ChannelBadge channel={s.channel} /> <input value={s.id} onChange={e => set(i, { id: e.target.value })} aria-label={`Step ${i + 1} id`} style={{ width: 120, marginLeft: 6 }} pattern="[a-z0-9][a-z0-9_-]{0,39}" title="Lowercase letters, digits, - or _" /></span>
                  <span style={{ display: "flex", gap: 6 }}>
                    <button type="button" className="btn sm" disabled={i === 0} onClick={() => move(i, i - 1)} aria-label="Move step up">↑</button>
                    <button type="button" className="btn sm" disabled={i === steps.length - 1} onClick={() => move(i, i + 1)} aria-label="Move step down">↓</button>
                    <button type="button" className="btn sm danger" onClick={() => onChange(steps.filter((_, k) => k !== i).map(x => (x.condStep === s.id ? { ...x, condStep: "" } : x)))} aria-label="Remove step">✕</button>
                  </span>
                </div>
                {tplOptions.length > 0 && (
                  <div className="field"><label>Message from</label>
                    <select value={s.template ?? ""} onChange={e => set(i, { template: e.target.value || null })}>
                      <option value="">Written here</option>{tplOptions.map(n => <option key={n} value={n}>Saved template: {n}</option>)}
                    </select></div>)}
                {s.template ? <div className="chain-note">Sends the <b>published</b> version of <b>{s.template}</b>.</div> : (
                  <>
                    {s.channel !== "sms" && <div className="field"><label>{s.channel === "email" ? "Subject" : "Title"}</label><input value={s.subject} onChange={e => set(i, { subject: e.target.value })} /></div>}
                    <div className="field"><label>Message</label><textarea rows={3} spellCheck={false} value={s.body} onChange={e => set(i, { body: e.target.value })} /></div>
                    <div className="field" style={{ maxWidth: 200 }}><label>Format</label>
                      <select value={s.format} onChange={e => set(i, { format: e.target.value })}>{FORMATS.map(f => <option key={f}>{f}</option>)}</select></div>
                  </>)}
                {i > 0 && (
                  <div>
                    <label>Only if the event matches (optional)</label>
                    {s.checks.map((c, k) => (
                      <div key={k} style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                        <input value={c.field} placeholder="payload field, e.g. plan" aria-label="Field" onChange={e => set(i, { checks: s.checks.map((x, y) => (y === k ? { ...x, field: e.target.value } : x)) })} />
                        <select value={c.op} aria-label="Operator" style={{ width: "auto" }} onChange={e => set(i, { checks: s.checks.map((x, y) => (y === k ? { ...x, op: e.target.value } : x)) })}>{OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                        {c.op !== "exists" && <input value={c.value} placeholder={c.op === "in" || c.op === "not_in" ? "a, b, c" : "value"} aria-label="Value" onChange={e => set(i, { checks: s.checks.map((x, y) => (y === k ? { ...x, value: e.target.value } : x)) })} />}
                        <button type="button" className="btn sm danger" aria-label="Remove check" onClick={() => set(i, { checks: s.checks.filter((_, y) => y !== k) })}>✕</button>
                      </div>))}
                    <button type="button" className="btn sm" onClick={() => set(i, { checks: [...s.checks, { field: "", op: "eq", value: "" }] })}>+ Add a check</button>
                  </div>)}
              </div>
            </div>
          </div>);
      })}
      {steps.length < MAX_STEPS && free.length > 0 && (
        <select value="" aria-label="Add a step" onChange={e => e.target.value && onChange([...steps, {
          id: slug(e.target.value, new Set(steps.map(s => s.id))), channel: e.target.value, subject: "", body: "", format: e.target.value === "email" ? "markdown" : "text",
          template: null, delay: steps.length ? "10m" : "", condStep: "", condStatus: steps.length ? "not_delivered" : "", checks: [], raw: {},
        }])} style={{ marginLeft: 36, width: "calc(100% - 36px)" }}>
          <option value="">+ Add a step…</option>{free.map(c => <option key={c} value={c}>{chLabel(c)}</option>)}
        </select>)}
    </div>
  );
}
