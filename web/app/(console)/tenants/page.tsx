"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAct, useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import { useToast } from "@/components/toast";
import { ActiveBadge, Card, EmptyState, Field, Modal, ShortId, errMsg } from "@/components/ui";

interface Tenant { id: string; name: string; slug: string; is_active: boolean; created_at: string; rate_limit_per_second: number | null; rate_burst: number | null; daily_quota: number | null }

function limits(t: Tenant) {
  const p: string[] = [];
  if (t.rate_limit_per_second != null) p.push(`${t.rate_limit_per_second}/s`);
  if (t.rate_burst != null) p.push(`burst ${t.rate_burst}`);
  if (t.daily_quota != null) p.push(`${t.daily_quota.toLocaleString()}/day`);
  return p.length ? p.join(" · ") : "platform default";
}
const num = (fd: FormData, k: string) => { const v = String(fd.get(k) || "").trim(); return v === "" ? null : Number(v); };

export default function Tenants() {
  const { refresh } = useAuth();
  const toast = useToast();
  const [modal, setModal] = useState<null | { kind: "new" } | { kind: "limits" | "rename"; t: Tenant }>(null);
  const q = useTq<Tenant[]>(["admin-tenants"], () => api("/admin/tenants"));
  const patch = useAct(({ id, body }: { id: string; body: unknown }) => api(`/admin/tenants/${id}`, { method: "PATCH", body }), { invalidate: ["admin-tenants"], onDone: () => { refresh(); } });
  const create = useAct((body: unknown) => api("/admin/tenants", { method: "POST", body }), { invalidate: ["admin-tenants"], onDone: () => { refresh(); } });
  const items = q.data ?? [];
  return (
    <>
      <Card title="Tenants" actions={<button className="btn primary" onClick={() => setModal({ kind: "new" })}>+ New tenant</button>} flush>
        {q.isLoading ? <EmptyState title="Loading…" />
          : q.isError ? <EmptyState title="Couldn't load tenants" hint={errMsg(q.error)} />
          : items.length === 0 ? <EmptyState title="No tenants yet" hint="Create the first customer organization." />
          : (
            <table>
              <thead><tr><th>Name</th><th>Slug</th><th>Tenant ID</th><th>Status</th><th>Event limits</th><th>Created</th><th /></tr></thead>
              <tbody>{items.map(t => (
                <tr key={t.id}><td style={{ fontWeight: 550 }}>{t.name}</td><td className="mono">{t.slug}</td><td><ShortId id={t.id} /></td>
                  <td><ActiveBadge active={t.is_active} /></td><td className="muted" style={{ fontSize: 12 }}>{limits(t)}</td><td className="muted">{fmtDate(t.created_at)}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button className="btn sm" onClick={() => setModal({ kind: "limits", t })}>Limits</button>{" "}
                    <button className="btn sm" onClick={() => setModal({ kind: "rename", t })}>Rename</button>{" "}
                    <button className={`btn sm ${t.is_active ? "danger" : ""}`} onClick={() => {
                      if (t.is_active && !window.confirm("Deactivate this tenant? Its API keys and console access stop working.")) return;
                      patch.mutate({ id: t.id, body: { is_active: !t.is_active } }, { onSuccess: () => toast(t.is_active ? "Tenant deactivated" : "Tenant activated", "ok") });
                    }}>{t.is_active ? "Deactivate" : "Activate"}</button></td></tr>))}</tbody>
            </table>)}
      </Card>
      {modal?.kind === "new" && (
        <Modal title="New tenant" submitLabel="Create" onClose={() => setModal(null)} onSubmit={async fd => { await create.mutateAsync({ name: fd.get("name"), slug: fd.get("slug") }); toast("Tenant created", "ok"); }}>
          <Field label="Name"><input name="name" required placeholder="Acme Corp" /></Field>
          <Field label="Slug"><input name="slug" required pattern="[a-z0-9][a-z0-9-]*" placeholder="acme-corp" title="lowercase letters, digits, hyphens" /></Field>
        </Modal>)}
      {modal?.kind === "rename" && (
        <Modal title="Rename tenant" onClose={() => setModal(null)} onSubmit={async fd => { await patch.mutateAsync({ id: modal.t.id, body: { name: fd.get("name") } }); toast("Tenant renamed", "ok"); }}>
          <Field label="Name"><input name="name" required defaultValue={modal.t.name} /></Field>
        </Modal>)}
      {modal?.kind === "limits" && (
        <Modal title={`Event limits · ${modal.t.name}`} onClose={() => setModal(null)} onSubmit={async fd => {
          await patch.mutateAsync({ id: modal.t.id, body: { rate_limit_per_second: num(fd, "rate"), rate_burst: num(fd, "burst"), daily_quota: num(fd, "quota") } });
          toast("Limits updated", "ok");
        }}>
          <div className="muted" style={{ marginBottom: 14, fontSize: 13 }}>How fast this tenant may send events to <code>POST /events</code>. Leave a field empty to use the platform default. Changes apply within about 30 seconds.</div>
          <div className="row">
            <Field label="Sustained rate (events / second)"><input name="rate" type="number" step="any" min="0.001" defaultValue={modal.t.rate_limit_per_second ?? ""} placeholder="platform default" /></Field>
            <Field label="Burst (events at once)"><input name="burst" type="number" min="1" defaultValue={modal.t.rate_burst ?? ""} placeholder="platform default" /></Field>
          </div>
          <Field label="Daily quota (events per UTC day)"><input name="quota" type="number" min="1" defaultValue={modal.t.daily_quota ?? ""} placeholder="platform default (unlimited unless configured)" /></Field>
        </Modal>)}
    </>
  );
}
