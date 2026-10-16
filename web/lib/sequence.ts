// Pure conversion between the sequence builder's form state and a workflow definition's `steps` (no imports, so it can be unit-tested).

export const MAX_STEPS = 10;

export interface Check { field: string; op: string; value: string }
export interface SeqStep {
  id: string; channel: string; subject: string; body: string; format: string; template: string | null;
  delay: string; condStep: string; condStatus: string; checks: Check[];
  raw: Record<string, unknown>;   // keys the builder does not edit survive a round trip
}
const parseValue = (op: string, v: string): unknown => {
  if (op === "exists") return true;
  if (op === "in" || op === "not_in") return v.split(",").map(x => x.trim()).filter(Boolean).map(x => (x !== "" && !isNaN(Number(x)) ? Number(x) : x));
  if (v !== "" && !isNaN(Number(v))) return Number(v);
  if (v === "true") return true;
  if (v === "false") return false;
  return v;
};
const showValue = (op: string, v: unknown) => (op === "exists" || v === undefined ? "" : Array.isArray(v) ? v.join(", ") : String(v));

export function fromSteps(steps: Record<string, any>[]): SeqStep[] {
  return steps.map((s, i) => {
    const w = s.when ?? {};
    const status = w.status === "unread" ? "not_delivered" : w.status === "read" ? "delivered" : (w.status ?? "");
    return {
      id: s.id ?? `step-${i + 1}`, channel: s.channel, subject: s.subject ?? "", body: s.body ?? "", format: s.format ?? "text",
      template: s.template ?? null, delay: s.delay !== undefined ? String(s.delay) : s.delay_seconds ? String(s.delay_seconds) : "",
      condStep: w.step ?? "", condStatus: status,
      checks: (w.payload ?? []).map((c: Record<string, unknown>) => ({ field: String(c.field ?? ""), op: String(c.op ?? "eq"), value: showValue(String(c.op ?? "eq"), c.value) })),
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

