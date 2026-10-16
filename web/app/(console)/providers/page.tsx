"use client";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { chLabel } from "@/lib/format";
import type { Provider } from "@/lib/types";
import { useToast } from "@/components/toast";
import { ActiveBadge, Card, ChannelBadge, EmptyState, Field, Modal, errMsg } from "@/components/ui";
import { ProviderLogo } from "@/components/provider";
import { HealthView, type Health } from "@/components/provider-health";

interface CatField { key: string; label: string; kind?: string; secret?: boolean; required?: boolean; default?: any; options?: string[]; help?: string }
interface Spec { id: string; channel: string; key: string; name: string; description: string; logo_url: string; builtin?: boolean; testable?: boolean; fields: CatField[] }
interface FullProvider extends Provider {
  name: string; config: Record<string, any>; secrets_set: string[]; used_by: { name: string }[];
  webhook_path?: string; webhook_url?: string; priority?: number;
}
const CHANNELS = ["email", "push", "sms", "in_app"];

export default function Providers() {
  const toast = useToast();
  const list = useTq<FullProvider[]>(["providers-full"], () => api("/providers"));
  const catalog = useTq<Spec[]>(["provider-catalog"], () => api("/provider-catalog"));
  const health = useTq<Health[]>(["provider-health"], () => api<Health[]>("/providers/health").catch(() => []));
  const del = useAct((id: string) => api(`/providers/${id}`, { method: "DELETE" }), { ok: "Provider deleted", invalidate: ["providers-full", "providers"] });
  const [modal, setModal] = useState<null | { pick: true } | { spec: Spec; p?: FullProvider }>(null);
  const hById = Object.fromEntries((health.data ?? []).map(h => [h.provider_id, h]));
  const items = list.data ?? [];

  return (
    <>
      <Card title="Providers" sub="Configure a provider once, then add it to any workflow."
        actions={<button className="btn primary" onClick={() => setModal({ pick: true })}>+ Add provider</button>} flush>
        {list.isLoading ? <EmptyState title="Loading…" />
          : list.isError ? <EmptyState title="Couldn't load providers" hint={errMsg(list.error)} />
          : items.length === 0 ? <EmptyState title="No providers yet" hint="Choose one from the catalog, enter its settings, then add it to a workflow." />
          : items.map(p => {
            const users = [...new Set(p.used_by.map(u => u.name))];
            return (
              <div className="provider-card" key={p.id}>
                <ProviderLogo p={p} />
                <div className="grow">
                  <div style={{ fontWeight: 600 }}>{p.label} <ChannelBadge channel={p.channel} /> <ActiveBadge active={!!p.is_active} /></div>
                  <div className="muted">{p.catalog?.name || p.name}{users.length ? ` · used by ${users.join(", ")}` : " · not used by any workflow yet"}</div>
                  <HealthView h={hById[p.id]} />
                  <Receipts p={p} />
                </div>
                <button className="btn sm" onClick={() => {
                  const spec = catalog.data?.find(c => c.channel === p.channel && c.key === p.name);
                  if (!spec) { toast(`'${p.name}' is no longer in the catalog`, "err"); return; }
                  setModal({ spec, p });
                }}>Edit</button>
                <button className="btn sm danger" onClick={() => window.confirm("Delete this provider?") && del.mutate(p.id)}>Delete</button>
              </div>);
          })}
      </Card>
      {modal && "pick" in modal && <Picker catalog={catalog.data ?? []} onClose={() => setModal(null)} onPick={spec => setModal({ spec })} />}
      {modal && "spec" in modal && <ProviderModal spec={modal.spec} p={modal.p} onClose={() => setModal(null)} />}
    </>
  );
}


function Receipts({ p }: { p: FullProvider }) {
  const toast = useToast();
  if (!p.webhook_path) return null;
  const url = p.webhook_url || location.origin + p.webhook_path;
  const auto = p.name === "twilio";
  const keyField = ({ resend: "webhook_secret", sendgrid: "webhook_public_key" } as Record<string, string>)[p.name];
  const on = auto ? !!p.webhook_url : p.name === "ses" ? !!p.config.sns_topic_arn : p.secrets_set.includes(keyField);
  const hint = auto
    ? (p.webhook_url ? "Pulse registers this callback with Twilio on every message." : "Set PULSE_API__PUBLIC_URL so Twilio can report delivery back to Pulse.")
    : p.name === "ses" ? "Subscribe this URL (HTTPS) to your SNS topic and enter the topic ARN via Edit; Pulse confirms the subscription itself."
    : `Add this URL in ${p.catalog?.name || p.name}'s webhook settings, then paste the signing key via Edit.`;
  return (
    <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
      {on ? "✓ Delivery receipts on" : "○ Delivery confirmation off — messages stay “sent”"} · {hint}
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 4 }}>
        <code className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 420 }}>{url}</code>
        <button type="button" className="btn sm" onClick={async () => {
          try { await navigator.clipboard.writeText(url); toast("Webhook URL copied", "ok"); } catch { toast("Copy failed — select the URL manually", "err"); }
        }}>Copy</button>
      </div>
    </div>
  );
}

function Picker({ catalog, onClose, onPick }: { catalog: Spec[]; onClose: () => void; onPick: (s: Spec) => void }) {
  const [ch, setCh] = useState("all");
  return (
    <Modal title="Choose a provider" width="760px" onClose={onClose} footer={false}>
      <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
        {["all", ...CHANNELS].map(c => <button key={c} type="button" className={`btn sm ${c === ch ? "primary" : ""}`} onClick={() => setCh(c)}>{c === "all" ? "All" : chLabel(c)}</button>)}
      </div>
      <div className="catalog-grid">
        {catalog.filter(c => ch === "all" || c.channel === ch).map(c => (
          <button type="button" className="catalog-card" key={c.id} onClick={() => onPick(c)}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={c.logo_url} alt="" />
            <div className="name">{c.name}</div><div><ChannelBadge channel={c.channel} /></div><div className="desc">{c.description}</div>
          </button>))}
      </div>
    </Modal>
  );
}

function FieldInput({ f, p }: { f: CatField; p?: FullProvider }) {
  const name = (f.secret ? "secret__" : "config__") + f.key;
  const saved = !!(f.secret && p?.secrets_set?.includes(f.key));
  const req = f.required && !saved;
  const value = f.secret ? "" : (p?.config?.[f.key] ?? f.default ?? "");
  const ph = saved ? "••••••••  (saved — leave blank to keep)" : "";
  if (f.kind === "boolean") return <label className="check"><input type="checkbox" name={name} defaultChecked={value === true || value === "true"} /> {f.label}</label>;
  return (
    <Field label={f.label + (f.required ? " *" : "")} hint={f.help}>
      {f.kind === "textarea" ? <textarea name={name} rows={6} spellCheck={false} autoComplete="off" required={req} placeholder={ph} />
        : f.kind === "select" ? <select name={name} defaultValue={value}>{f.options!.map(o => <option key={o}>{o}</option>)}</select>
        : <input name={name} type={f.secret ? "password" : f.kind === "number" ? "number" : "text"} autoComplete={f.secret ? "new-password" : "off"} defaultValue={value} required={req} placeholder={ph} />}
    </Field>
  );
}

function toBody(fd: FormData, spec: Spec, p?: FullProvider) {
  const config: Record<string, unknown> = {}, secrets: Record<string, string> = {};
  for (const f of spec.fields) {
    const key = (f.secret ? "secret__" : "config__") + f.key;
    if (f.kind === "boolean") { config[f.key] = fd.get(key) === "on"; continue; }
    const v = fd.get(key) as string | null;
    if (f.secret) { if (v) secrets[f.key] = v; }
    else if (v !== null && v !== "") config[f.key] = f.kind === "number" ? Number(v) : v;
  }
  return { channel: spec.channel, name: spec.key, label: fd.get("label"), config, secrets, priority: p?.priority ?? 0, is_active: fd.get("is_active") === "on" };
}

function ProviderModal({ spec, p, onClose }: { spec: Spec; p?: FullProvider; onClose: () => void }) {
  const toast = useToast();
  const formRef = useRef<HTMLDivElement>(null);
  const [test, setTest] = useState<{ busy?: boolean; ok?: boolean; text?: string }>({});
  const save = useAct((body: unknown) => p ? api(`/providers/${p.id}`, { method: "PUT", body }) : api("/providers", { method: "POST", body }),
    { invalidate: ["providers-full", "providers", "provider-health"] });
  const runTest = async () => {
    const form = formRef.current?.closest("form");
    if (!form) return;
    const body = toBody(new FormData(form), spec, p);
    setTest({ busy: true, text: "Testing…" });
    try {
      const r = await api<{ ok: boolean; detail: string }>(`/provider-catalog/${spec.channel}/${spec.key}/test`, { method: "POST", body: { config: body.config, secrets: body.secrets, provider_id: p?.id ?? null } });
      setTest({ ok: r.ok, text: (r.ok ? "✓ " : "✕ ") + r.detail });
    } catch (e) { setTest({ ok: false, text: errMsg(e) }); }
  };
  return (
    <Modal title={p ? "Edit provider" : `Set up ${spec.name}`} submitLabel={p ? "Save" : "Save provider"} onClose={onClose}
      onSubmit={async fd => { await save.mutateAsync(toBody(fd, spec, p)); toast(p ? "Provider updated" : "Provider saved", "ok"); }}>
      <div ref={formRef}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 16 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="provider-logo" src={spec.logo_url} alt="" />
          <div><div style={{ fontWeight: 600 }}>{spec.name}</div><div className="muted">{spec.description}</div></div>
        </div>
        <Field label="Name" hint="How you'll recognise this provider when adding it to workflows.">
          <input name="label" required maxLength={200} defaultValue={p?.label || spec.name} placeholder="e.g. Marketing email" />
        </Field>
        {spec.builtin && <div className="muted" style={{ marginBottom: 12 }}>No setup needed.</div>}
        {spec.fields.map(f => <FieldInput key={f.key} f={f} p={p} />)}
        <label className="check"><input type="checkbox" name="is_active" defaultChecked={p?.is_active !== false} /> Active</label>
        {spec.testable && (
          <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center" }}>
            <button type="button" className="btn sm" disabled={test.busy} onClick={runTest}>Test connection</button>
            <span className="muted" style={{ color: test.ok === undefined ? undefined : test.ok ? "var(--green)" : "var(--red)" }}>{test.text}</span>
          </div>)}
      </div>
    </Modal>
  );
}
