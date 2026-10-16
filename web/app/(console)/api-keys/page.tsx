"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import { useToast } from "@/components/toast";
import { Card, EmptyState, Field, Modal, errMsg } from "@/components/ui";
import { RevealBox } from "@/components/reveal";

interface Key {
  id: string; name: string; prefix: string; scopes: string[]; status: string;
  expires_at?: string | null; last_used_at?: string | null; replaced_by?: string | null; rotated_from?: string | null;
}
interface Minted { name: string; key: string; previous_key_valid_until?: string }
const SCOPES = [
  { id: "ingest", label: "Ingest", help: "Send events (POST /events). For the services that produce notifications.", checked: true },
  { id: "read", label: "Read", help: "Look things up: notifications, events, metrics, providers, workflows." },
  { id: "client", label: "Client", help: "For end-user apps: live stream, acknowledgements, device registration, preferences." },
  { id: "admin", label: "Admin", help: "Everything above, plus changing providers, workflows, templates and retrying dead letters." },
];
const STATUS: Record<string, string> = { active: "green", expired: "amber", revoked: "red" };

export default function ApiKeys() {
  const toast = useToast();
  const [modal, setModal] = useState<null | "new" | { rotate: Key }>(null);
  const [reveal, setReveal] = useState<{ k: Minted; heading: string } | null>(null);
  const q = useTq<Key[]>(["api-keys"], () => api("/tenant/api-keys"));
  const revoke = useAct((id: string) => api(`/tenant/api-keys/${id}`, { method: "DELETE" }), { ok: "Key revoked", invalidate: ["api-keys"] });
  const create = useAct((body: unknown) => api<Minted>("/tenant/api-keys", { method: "POST", body }), { invalidate: ["api-keys"] });
  const rotate = useAct(({ id, grace_hours }: { id: string; grace_hours: number }) => api<Minted>(`/tenant/api-keys/${id}/rotate`, { method: "POST", body: { grace_hours } }), { invalidate: ["api-keys"] });
  const items = q.data ?? [];
  return (
    <>
      <Card title="API keys" sub={<>Services authenticate with <span className="mono">X-API-Key</span>. Give each key only the scopes it needs. Keys are hashed at rest and shown once.</>}
        actions={<button className="btn primary" onClick={() => setModal("new")}>+ Create key</button>} flush>
        {q.isLoading ? <EmptyState title="Loading…" />
          : q.isError ? <EmptyState title="Couldn't load API keys" hint={errMsg(q.error)} />
          : items.length === 0 ? <EmptyState title="No API keys" hint="Create a key so services like Quotient can publish events for this tenant." />
          : (
            <table>
              <thead><tr><th>Name</th><th>Key</th><th>Scopes</th><th>Status</th><th>Expires</th><th>Last used</th><th /></tr></thead>
              <tbody>{items.map(k => (
                <tr key={k.id}>
                  <td style={{ fontWeight: 550 }}>{k.name}
                    {k.replaced_by && <div className="muted" style={{ fontWeight: 400, fontSize: 12 }}>being replaced — switch clients to the new key</div>}
                    {k.rotated_from && <div className="muted" style={{ fontWeight: 400, fontSize: 12 }}>rotated from an earlier key</div>}</td>
                  <td className="mono muted">{k.prefix}…</td>
                  <td>{k.scopes.map(s => <span key={s} className="badge plain" style={{ marginRight: 4 }}>{s}</span>)}</td>
                  <td><span className={`badge ${STATUS[k.status] || ""} plain`}>{k.status}</span></td>
                  <td className="muted">{k.expires_at ? fmtDate(k.expires_at) : "never"}</td>
                  <td className="muted">{k.last_used_at ? fmtDate(k.last_used_at) : "never"}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>{k.status === "active" && <>
                    <button className="btn sm" onClick={() => setModal({ rotate: k })}>Rotate</button>{" "}
                    <button className="btn sm danger" onClick={() => window.confirm("Revoke this key? Services using it will stop authenticating immediately.") && revoke.mutate(k.id)}>Revoke</button></>}</td>
                </tr>))}</tbody>
            </table>)}
      </Card>
      {reveal && <RevealBox copy={reveal.k.key}><b>{reveal.k.name}</b> — {reveal.heading}
        <div className="mono" style={{ marginTop: 6 }}>{reveal.k.key}</div></RevealBox>}

      {modal === "new" && (
        <Modal title="Create API key" submitLabel="Create" onClose={() => setModal(null)} onSubmit={async fd => {
          const scopes = fd.getAll("scope");
          if (!scopes.length) throw new Error("Choose at least one scope");
          const k = await create.mutateAsync({ name: fd.get("name"), scopes, ...(fd.get("expires") ? { expires_in_days: Number(fd.get("expires")) } : {}) });
          setReveal({ k, heading: "copy this key now, it will not be shown again:" }); toast("API key created", "ok");
        }}>
          <Field label="Name"><input name="name" required placeholder="quotient-prod" /></Field>
          <Field label="What may this key do?">
            {SCOPES.map(s => (
              <label className="check" key={s.id} style={{ alignItems: "flex-start", margin: "6px 0" }}>
                <input type="checkbox" name="scope" value={s.id} defaultChecked={s.checked} />
                <span><b>{s.label}</b><div className="muted" style={{ fontSize: 12 }}>{s.help}</div></span>
              </label>))}
          </Field>
          <Field label="Expires"><select name="expires"><option value="">Never</option><option value="30">In 30 days</option><option value="90">In 90 days</option><option value="365">In a year</option></select></Field>
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>The full key is displayed once after creation: store it in the calling service&apos;s secrets.</p>
        </Modal>)}
      {modal && typeof modal === "object" && (
        <Modal title={`Rotate “${modal.rotate.name}”`} submitLabel="Rotate key" onClose={() => setModal(null)} onSubmit={async fd => {
          const k = await rotate.mutateAsync({ id: modal.rotate.id, grace_hours: Number(fd.get("grace")) });
          setReveal({ k, heading: `copy the new key now, it will not be shown again. The old key works until ${fmtDate(k.previous_key_valid_until)}.` }); toast("Key rotated", "ok");
        }}>
          <p className="muted" style={{ marginTop: 0 }}>A new key with the same scopes{modal.rotate.expires_at ? " and expiry" : ""} is issued now. The old key keeps working for the grace period so you can update your services without downtime.</p>
          <Field label="Keep the old key working for">
            <select name="grace" defaultValue="24"><option value="0">Not at all (revoke now)</option><option value="1">1 hour</option><option value="24">24 hours</option><option value="72">3 days</option><option value="168">7 days</option></select>
          </Field>
        </Modal>)}
    </>
  );
}
