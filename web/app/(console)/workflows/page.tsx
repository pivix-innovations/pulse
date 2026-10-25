"use client";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import type { Provider } from "@/lib/types";
import { useToast } from "@/components/toast";
import { ActiveBadge, Card, ChannelBadge, EmptyState, Field, Modal, ShortId, errMsg } from "@/components/ui";
import { DEFAULT_DEFINITION, WorkflowEditor, type Tpl } from "@/components/workflow-editor";

interface Workflow { id: string; name: string; event_type: string; definition: Record<string, any>; is_active: boolean; updated_at?: string; created_at?: string }

export default function Workflows() {
  const q = useTq<Workflow[]>(["workflows"], () => api("/workflows"));
  const [modal, setModal] = useState<null | { w?: Workflow }>(null);
  const del = useAct((id: string) => api(`/workflows/${id}`, { method: "DELETE" }), { ok: "Workflow deleted", invalidate: ["workflows"] });
  const items = q.data ?? [];
  return (
    <>
      <Card title="Workflows" actions={<button className="btn primary" onClick={() => setModal({})}>+ New workflow</button>} flush>
        {q.isLoading ? <EmptyState title="Loading…" />
          : q.isError ? <EmptyState title="Couldn't load workflows" hint={errMsg(q.error)} />
          : items.length === 0 ? <EmptyState title="No workflows yet" hint="A workflow maps an event type to channels, templates, and fallbacks." />
          : (
            <table>
              <thead><tr><th>Name</th><th>Event type</th><th>Channels</th><th>Status</th><th>Updated</th><th /></tr></thead>
              <tbody>{items.map(w => {
                const steps: any[] = w.definition.steps || [];
                const channels: string[] = (steps.length ? steps : w.definition.channels || []).map((c: any) => c.channel);
                const dg = w.definition.digest;
                return (
                  <tr key={w.id}>
                    <td style={{ fontWeight: 550 }}>{w.name}<div className="muted" style={{ fontWeight: 400 }}><ShortId id={w.id} /></div></td>
                    <td className="mono">{w.event_type}</td>
                    <td>
                      {dg && <span className="muted" style={{ fontSize: 12 }}>digest {dg.window || dg.window_seconds + "s"}: </span>}
                      {steps.length > 0 && <span className="muted" style={{ fontSize: 12 }}>sequence: </span>}
                      {channels.length ? channels.map((c, i) => <span key={i}>{i > 0 && (steps.length ? " → " : " ")}<ChannelBadge channel={c} /></span>) : "—"}
                    </td>
                    <td><ActiveBadge active={w.is_active} /></td>
                    <td className="muted">{fmtDate(w.updated_at || w.created_at)}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <button className="btn sm" onClick={() => setModal({ w })}>Edit</button>{" "}
                      <button className="btn sm danger" onClick={() => window.confirm("Delete this workflow?") && del.mutate(w.id)}>Delete</button>
                    </td>
                  </tr>);
              })}</tbody>
            </table>
          )}
      </Card>
      {modal && <WorkflowModal w={modal.w} onClose={() => setModal(null)} />}
    </>
  );
}

function WorkflowModal({ w, onClose }: { w?: Workflow; onClose: () => void }) {
  const toast = useToast();
  const providers = useTq<Provider[]>(["providers"], () => api<Provider[]>("/providers").catch(() => []));
  const templates = useTq<Tpl[]>(["templates"], () => api<Tpl[]>("/templates").catch(() => []));
  const read = useRef<() => Record<string, any>>(() => ({}));
  const save = useAct((body: unknown) => w ? api(`/workflows/${w.id}`, { method: "PUT", body }) : api("/workflows", { method: "POST", body }), { invalidate: ["workflows"] });
  const ready = !providers.isLoading && !templates.isLoading;
  return (
    <Modal title={w ? "Edit workflow" : "New workflow"} submitLabel={w ? "Save" : "Create"} width="760px" onClose={onClose}
      onSubmit={async fd => {
        await save.mutateAsync({ name: fd.get("name"), event_type: fd.get("event_type"), definition: read.current(), is_active: fd.get("is_active") === "on" });
        toast(w ? "Workflow updated" : "Workflow created", "ok");
      }}>
      <div className="row">
        <Field label="Name"><input name="name" required defaultValue={w?.name} placeholder="welcome" /></Field>
        <Field label="Event type"><input name="event_type" required defaultValue={w?.event_type} placeholder="user.signed_up" /></Field>
      </div>
      <label className="check" style={{ marginBottom: 14 }}><input type="checkbox" name="is_active" defaultChecked={w?.is_active !== false} /> Active</label>
      {ready && <WorkflowEditor initial={w?.definition ?? DEFAULT_DEFINITION} providers={providers.data ?? []} templates={templates.data ?? []} getRef={r => { read.current = r; }} />}
    </Modal>
  );
}
