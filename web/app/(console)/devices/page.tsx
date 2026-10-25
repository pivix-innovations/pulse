"use client";
import { useState } from "react";
import { api, qs } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useAct, useTq } from "@/lib/hooks";
import { Badge, Card, EmptyState, Field, ShortId, errMsg } from "@/components/ui";

interface Device { id: string; platform: string; token_hint: string; last_seen_at?: string | null; created_at?: string | null }

export default function Devices() {
  const [res, setRes] = useState<{ id: string; recipient_id: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [recipient, setRecipient] = useState("");
  const reg = useAct((body: Record<string, unknown>) => api<{ id: string; recipient_id: string }>("/devices", { method: "POST", body }),
    { ok: "Device registered", invalidate: ["devices"], onDone: setRes });
  const list = useTq<Device[]>(["devices", recipient], () => api(`/devices${qs({ recipient })}`), { enabled: !!recipient });
  const del = useAct((id: string) => api(`/devices/${id}`, { method: "DELETE" }), { ok: "Device removed", invalidate: ["devices"] });

  return (
    <>
      <Card style={{ marginBottom: 20 }}>
        <form className="toolbar" onSubmit={e => { e.preventDefault(); setRecipient(draft.trim()); }}>
          <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Recipient external ref (e.g. user-123)" required spellCheck={false} style={{ flex: 1, minWidth: 220 }} aria-label="Recipient" />
          <button className="btn primary" type="submit">Show devices</button>
        </form>
      </Card>

      {recipient && (
        <Card title={<>Devices for <span className="mono">{recipient}</span></>} flush style={{ marginBottom: 20 }}>
          {list.isLoading ? <EmptyState title="Loading…" />
            : list.isError ? <EmptyState title="Couldn't load devices" hint={errMsg(list.error)} />
            : !list.data?.length ? <EmptyState title="No devices" hint="Push notifications need a registered device token." />
            : (
              <table>
                <thead><tr><th>Platform</th><th>Token</th><th>Last seen</th><th>Registered</th><th /></tr></thead>
                <tbody>{list.data.map(d => (
                  <tr key={d.id}><td><Badge status={d.platform} color="" /></td><td className="mono">{d.token_hint}</td>
                    <td className="muted">{fmtDate(d.last_seen_at)}</td><td className="muted">{fmtDate(d.created_at)}</td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn sm danger" onClick={() => window.confirm("Remove this device? It stops receiving push notifications.") && del.mutate(d.id)}>Remove</button>
                    </td></tr>))}</tbody>
              </table>)}
        </Card>)}

      <div style={{ maxWidth: 640 }}>
        <Card title="Register device token" sub="Push notifications target the recipient's registered device tokens.">
          <form onSubmit={e => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            const ref = String(fd.get("external_ref"));
            reg.mutate({ external_ref: ref, platform: fd.get("platform"), token: fd.get("token") }, { onSuccess: () => { setDraft(ref); setRecipient(ref); } });
          }}>
            <Field label="Recipient external ref"><input name="external_ref" required placeholder="user-123" spellCheck={false} /></Field>
            <div className="row"><Field label="Platform"><select name="platform"><option value="android">Android</option><option value="ios">iOS</option><option value="web">Web</option></select></Field></div>
            <Field label="Device token"><textarea name="token" rows={3} required spellCheck={false} placeholder="FCM registration token" /></Field>
            <button className="btn primary" type="submit" disabled={reg.isPending}>Register device</button>
          </form>
          {res && <div className="muted" style={{ fontSize: 12.5, marginTop: 14 }}>Device <ShortId id={res.id} /> registered for recipient <ShortId id={res.recipient_id} /></div>}
        </Card>
      </div>
    </>
  );
}
