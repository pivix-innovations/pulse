"use client";
import { useMemo, useState } from "react";
import { chLabel } from "@/lib/format";
import type { Provider } from "@/lib/types";
import { ChannelBadge } from "./ui";
import { ProviderLogo } from "./provider";

export const WF_CHANNELS = ["email", "push", "sms", "in_app"] as const;
const WF_FORMATS = ["markdown", "html", "text"];
const DEFAULT_BODY: Record<string, string> = { email: "Hi {{ recipient.external_ref }},\n\n", push: "", sms: "", in_app: "" };

export interface Tpl { name: string; channel: string; is_active: boolean; published_version: number | null }
interface Ch { channel: string; subject: string; body: string; format: string; template: string | null; fallbackOnly: boolean }
interface Digest { window: string; max_items: number | string; key: string }
interface St { channels: Ch[]; fallbacks: Record<string, string>; chains: Record<string, string[]>; digest: Digest | null }
type Def = Record<string, any>;

const SEQUENCE_EXAMPLE = {
  steps: [
    { id: "nudge", channel: "in_app", subject: "Hi {{ recipient.external_ref }}", body: "You have a new message", format: "text" },
    { id: "email", channel: "email", subject: "You have a new message", body: "Open the app to read it.", format: "markdown", delay: "10m", when: { status: "unread" } },
  ],
};
export const DEFAULT_DEFINITION = { channels: [{ channel: "in_app", subject: "", body: "", format: "text" }] };

function fromDefinition(def: Def): St {
  const channels: Ch[] = [
    ...(def.channels || []).map((c: Def) => ({ ...c, fallbackOnly: false })),
    ...(def.fallback_templates || []).map((c: Def) => ({ ...c, fallbackOnly: true })),
  ].map((c: Def) => ({ channel: c.channel, subject: c.subject || "", body: c.body || "", format: c.format || "markdown", template: c.template || null, fallbackOnly: c.fallbackOnly }));
  const d = def.digest;
  return {
    channels, fallbacks: { ...(def.fallbacks || {}) }, chains: JSON.parse(JSON.stringify(def.provider_chains || {})),
    digest: d ? { window: d.window || (d.window_seconds ? `${d.window_seconds}s` : "10m"), max_items: d.max_items || 100, key: d.key || "" } : null,
  };
}

function toDefinition(st: St): Def {
  const enc = (c: Ch) => c.template ? { channel: c.channel, template: c.template }
    : { channel: c.channel, subject: c.subject || null, body: c.body, format: c.format };
  const present = new Set(st.channels.map(c => c.channel));
  const def: Def = {
    channels: st.channels.filter(c => !c.fallbackOnly).map(enc),
    fallbacks: Object.fromEntries(Object.entries(st.fallbacks).filter(([s, d]) => d && present.has(s) && present.has(d))),
  };
  const fb = st.channels.filter(c => c.fallbackOnly).map(enc);
  if (fb.length) def.fallback_templates = fb;
  if (st.digest) def.digest = { window: st.digest.window || "10m", max_items: +st.digest.max_items || 100, ...(st.digest.key ? { key: st.digest.key } : {}) };
  const chains = Object.fromEntries(Object.entries(st.chains).filter(([ch, ids]) => ids.length && present.has(ch)));
  if (Object.keys(chains).length) def.provider_chains = chains;
  return def;
}

/** Controlled by the parent through `getRef`: call it to read the current definition (throws on bad JSON). */
export function WorkflowEditor({ initial, providers, templates, getRef }: {
  initial: Def; providers: Provider[]; templates: Tpl[]; getRef: (read: () => Def) => void;
}) {
  const isSeq = Array.isArray(initial.steps) && initial.steps.length > 0;
  const [st, setSt] = useState<St>(() => fromDefinition(initial));
  const [sequence, setSequence] = useState(isSeq);
  const [jsonMode, setJsonMode] = useState(isSeq);
  const [jsonText, setJsonText] = useState(() => JSON.stringify(initial, null, 2));
  const [jsonErr, setJsonErr] = useState(false);
  const byId = useMemo(() => Object.fromEntries(providers.map(p => [p.id, p])), [providers]);
  const [dragFrom, setDragFrom] = useState<{ ch: string; i: number } | null>(null);

  getRef(() => {
    if (jsonMode) { try { return JSON.parse(jsonText); } catch { throw new Error("Definition is not valid JSON"); } }
    return toDefinition(st);
  });

  const upd = (fn: (s: St) => void) => setSt(s => { const n = structuredClone(s); fn(n); return n; });

  if (jsonMode) return (
    <>
      {sequence && <div className="chain-note" style={{ marginBottom: 10 }}><b>Sequence workflow.</b> Each step sends on a channel, optionally after a{" "}
        <code>delay</code> (<code>&quot;10m&quot;</code>, <code>&quot;2h&quot;</code>, seconds) and only <code>when</code> its condition holds:{" "}
        <code>{`{"status": "unread"}`}</code> tests the previous step (or name one with <code>&quot;step&quot;</code>), and{" "}
        <code>{`{"payload": [{"field": "plan", "op": "eq", "value": "pro"}]}`}</code> tests the event. One step per channel, up to 10.</div>}
      <div className="field"><label>Definition (JSON)</label>
        <textarea rows={16} spellCheck={false} value={jsonText} onChange={e => setJsonText(e.target.value)} /></div>
      {jsonErr && <div className="chain-note" style={{ color: "var(--red)" }}>That JSON isn&apos;t valid yet</div>}
      {!sequence && <button type="button" className="btn sm" onClick={() => {
        try { setSt(fromDefinition(JSON.parse(jsonText))); setJsonErr(false); setJsonMode(false); } catch { setJsonErr(true); }
      }}>← Back to form</button>}
    </>
  );

  const free = WF_CHANNELS.filter(ch => !st.channels.some(c => c.channel === ch));
  return (
    <>
      <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>Messages are templates: use <code>{"{{ recipient.external_ref }}"}</code> and any field from the event payload, e.g. <code>{"{{ plan }}"}</code>.</div>
      {st.channels.length === 0 && <div className="chain-note" style={{ marginBottom: 10 }}>No channels yet — add the first one below.</div>}
      {st.channels.map((c, i) => {
        const others = st.channels.filter(o => o.channel !== c.channel);
        const names = [...new Set(templates.filter(t => t.channel === c.channel && t.is_active && t.published_version).map(t => t.name))];
        const tplOptions = c.template && !names.includes(c.template) ? [...names, c.template] : names;
        const ids = st.chains[c.channel] || [];
        const options = providers.filter(p => p.channel === c.channel && p.is_active && !ids.includes(p.id));
        const move = (a: number, b: number) => upd(s => { const arr = (s.chains[c.channel] ||= []); [arr[a], arr[b]] = [arr[b], arr[a]]; });
        return (
          <div className="chain" key={c.channel}>
            <div className="chain-head">
              <span><ChannelBadge channel={c.channel} /> {c.fallbackOnly && <span className="muted" style={{ fontWeight: 400 }}>fallback only</span>}</span>
              <button type="button" className="btn sm danger" onClick={() => upd(s => {
                const [gone] = s.channels.splice(i, 1); delete s.fallbacks[gone.channel]; delete s.chains[gone.channel];
                for (const [src, dst] of Object.entries(s.fallbacks)) if (dst === gone.channel) delete s.fallbacks[src];
              })}>Remove channel</button>
            </div>
            <label className="check" style={{ marginBottom: 10 }}>
              <input type="checkbox" checked={c.fallbackOnly} onChange={e => upd(s => { s.channels[i].fallbackOnly = e.target.checked; if (e.target.checked) delete s.fallbacks[c.channel]; })} />
              Only send as a fallback (not on every event)
            </label>
            {tplOptions.length > 0 && (
              <div className="field"><label>Message from</label>
                <select value={c.template ?? ""} onChange={e => upd(s => { s.channels[i].template = e.target.value || null; })}>
                  <option value="">Written here</option>
                  {tplOptions.map(n => <option key={n} value={n}>Saved template: {n}</option>)}
                </select></div>)}
            {c.template ? (
              <div className="chain-note" style={{ marginBottom: 10 }}>Sends the <b>published</b> version of <b>{c.template}</b> (in the recipient&apos;s language when a translation exists). Edit or roll it back on the Templates page.</div>
            ) : (
              <>
                {c.channel !== "sms" && <div className="field"><label>{c.channel === "email" ? "Subject" : "Title"}</label>
                  <input value={c.subject} onChange={e => upd(s => { s.channels[i].subject = e.target.value; })} placeholder="Welcome, {{ recipient.external_ref }}!" /></div>}
                <div className="field"><label>Message</label>
                  <textarea rows={4} spellCheck={false} value={c.body} onChange={e => upd(s => { s.channels[i].body = e.target.value; })} /></div>
              </>
            )}
            <div className="row">
              {!c.template && <div className="field"><label>Format</label>
                <select value={c.format} onChange={e => upd(s => { s.channels[i].format = e.target.value; })}>{WF_FORMATS.map(f => <option key={f}>{f}</option>)}</select></div>}
              {!c.fallbackOnly && others.length > 0 && <div className="field"><label>If not delivered, fall back to</label>
                <select value={st.fallbacks[c.channel] ?? ""} onChange={e => upd(s => { if (e.target.value) s.fallbacks[c.channel] = e.target.value; else delete s.fallbacks[c.channel]; })}>
                  <option value="">Nothing</option>{others.map(o => <option key={o.channel} value={o.channel}>{chLabel(o.channel)}</option>)}</select></div>}
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Providers <span className="muted" style={{ fontWeight: 400 }}>— tried top to bottom; next one only if the one above fails or times out</span></label>
              {ids.map((id, k) => {
                const p = byId[id];
                return (
                  <div className="chain-item" key={id} draggable
                    onDragStart={e => { setDragFrom({ ch: c.channel, i: k }); e.dataTransfer.effectAllowed = "move"; }}
                    onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
                    onDrop={e => { e.preventDefault(); if (!dragFrom || dragFrom.ch !== c.channel || dragFrom.i === k) return;
                      const from = dragFrom.i; upd(s => { const a = s.chains[c.channel]; const [m] = a.splice(from, 1); a.splice(k, 0, m); }); setDragFrom(null); }}>
                    <span className="muted" style={{ cursor: "grab" }} title="Drag to reorder">⋮⋮</span>
                    <span className="rank">{k + 1}</span>
                    {p && <ProviderLogo p={p} />}
                    <span className="grow">{p ? p.label : <span className="muted">Unknown provider</span>}</span>
                    <button type="button" className="btn sm" disabled={k === 0} aria-label="Move up" onClick={() => move(k, k - 1)}>↑</button>
                    <button type="button" className="btn sm" disabled={k === ids.length - 1} aria-label="Move down" onClick={() => move(k, k + 1)}>↓</button>
                    <button type="button" className="btn sm danger" aria-label="Remove" onClick={() => upd(s => { s.chains[c.channel].splice(k, 1); })}>✕</button>
                  </div>);
              })}
              {options.length > 0 && (
                <select value="" onChange={e => e.target.value && upd(s => { (s.chains[c.channel] ||= []).push(e.target.value); })}>
                  <option value="">+ Add a provider…</option>
                  {options.map(p => <option key={p.id} value={p.id}>{p.label} ({p.catalog?.name || p.name})</option>)}
                </select>)}
              {!ids.length && <div className="chain-note">{options.length ? "None chosen — Pulse's default for this channel is used." : `No saved ${chLabel(c.channel)} providers — Pulse's default is used. Add one on the Providers page to choose.`}</div>}
            </div>
          </div>);
      })}
      <div className="card" style={{ margin: "12px 0", padding: 12 }}>
        <label className="check"><input type="checkbox" checked={!!st.digest} onChange={e => upd(s => { s.digest = e.target.checked ? { window: "10m", max_items: 100, key: "" } : null; })} />
          Batch events into a digest <span className="muted" style={{ fontWeight: 400 }}>— one message per recipient instead of one per event</span></label>
        {st.digest && (
          <>
            <div className="row" style={{ marginTop: 10 }}>
              <div className="field"><label>Send after</label><input value={st.digest.window} placeholder="10m" onChange={e => upd(s => { s.digest!.window = e.target.value; })} /></div>
              <div className="field"><label>…or once this many events</label><input type="number" min={2} max={1000} value={st.digest.max_items} onChange={e => upd(s => { s.digest!.max_items = e.target.value; })} /></div>
              <div className="field"><label>Group by field (optional)</label><input value={st.digest.key} placeholder="post_id" onChange={e => upd(s => { s.digest!.key = e.target.value; })} /></div>
            </div>
            <div className="muted" style={{ fontSize: 12 }}>Templates can use <code>{"{{ digest.count }}"}</code>, <code>{"{% for e in digest.items %}…{% endfor %}"}</code>, <code>digest.first</code> and <code>digest.last</code>; other variables come from the latest event.</div>
          </>)}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between" }}>
        {free.length ? (
          <select value="" onChange={e => e.target.value && upd(s => s.channels.push({ channel: e.target.value, subject: "", body: DEFAULT_BODY[e.target.value] || "", format: e.target.value === "email" ? "markdown" : "text", template: null, fallbackOnly: false }))}>
            <option value="">+ Add a channel…</option>{free.map(ch => <option key={ch} value={ch}>{chLabel(ch)}</option>)}
          </select>) : <span />}
        <span style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn sm" title="Send in steps with delays and conditions" onClick={() => {
            if (st.channels.length && !window.confirm("Switch to a sequence? The channels above are replaced by the example steps.")) return;
            setSequence(true); setJsonMode(true); setJsonText(JSON.stringify(SEQUENCE_EXAMPLE, null, 2));
          }}>Make it a sequence…</button>
          <button type="button" className="btn sm" onClick={() => { setJsonText(JSON.stringify(toDefinition(st), null, 2)); setJsonMode(true); }}>Edit as JSON</button>
        </span>
      </div>
    </>
  );
}
