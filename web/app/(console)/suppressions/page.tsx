"use client";
import { useState } from "react";
import { api, qs } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import { useToast } from "@/components/toast";
import { Badge, Card, ChannelBadge, EmptyState, Field, Modal, Pager, errMsg } from "@/components/ui";

interface Sup { id: string; address: string; channel: string; reason: string; detail?: string | null; created_at: string }
const REASON_COLOR: Record<string, string> = { bounce: "red", complaint: "red", unsubscribe: "amber", manual: "" };
const PAGE = 25;

export default function Suppressions() {
  const toast = useToast();
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);
  const q = useTq<Sup[]>(["suppressions", offset], () => api(`/suppressions${qs({ limit: PAGE, offset })}`));
  const del = useAct((id: string) => api(`/suppressions/${id}`, { method: "DELETE" }), { ok: "Removed", invalidate: ["suppressions"] });
  const add = useAct((body: unknown) => api("/suppressions", { method: "POST", body }), { invalidate: ["suppressions"] });
  const items = q.data ?? [];
  return (
    <>
      <Card title="Suppression list" sub="Bounces, spam complaints, unsubscribes and STOP replies are added automatically. Sends to these addresses are skipped."
        actions={<button className="btn primary" onClick={() => setAdding(true)}>+ Suppress an address</button>} flush>
        {q.isLoading ? <EmptyState title="Loading…" />
          : q.isError ? <EmptyState title="Couldn't load the suppression list" hint={errMsg(q.error)} />
          : items.length === 0 && offset === 0 ? <EmptyState title="Nothing suppressed" hint="Hard bounces and spam complaints will appear here automatically." />
          : (
            <>
              <table>
                <thead><tr><th>Address</th><th>Channel</th><th>Reason</th><th>Detail</th><th>Added</th><th /></tr></thead>
                <tbody>{items.map(x => (
                  <tr key={x.id}><td className="mono">{x.address}</td><td><ChannelBadge channel={x.channel} /></td>
                    <td><Badge status={x.reason} color={REASON_COLOR[x.reason] ?? ""} /></td>
                    <td className="muted" style={{ maxWidth: 300, whiteSpace: "normal" }}>{x.detail || "—"}</td>
                    <td className="muted">{fmtDate(x.created_at)}</td>
                    <td style={{ textAlign: "right" }}><button className="btn sm danger" onClick={() => window.confirm("Remove this address from the list? Messages to it will be sent again.") && del.mutate(x.id)}>Remove</button></td></tr>))}</tbody>
              </table>
              <Pager offset={offset} count={items.length} page={PAGE} onChange={setOffset} />
            </>
          )}
      </Card>
      {adding && (
        <Modal title="Suppress an address" submitLabel="Suppress" onClose={() => setAdding(false)} onSubmit={async fd => {
          await add.mutateAsync({ channel: fd.get("channel"), address: String(fd.get("address")).trim(), detail: fd.get("detail") || null });
          toast("Address suppressed", "ok"); setOffset(0);
        }}>
          <div className="row">
            <Field label="Channel"><select name="channel"><option>email</option><option>sms</option></select></Field>
            <div style={{ flex: 2 }}><Field label="Address or number"><input name="address" required placeholder="someone@example.com or +15551234567" spellCheck={false} /></Field></div>
          </div>
          <Field label="Note (optional)"><input name="detail" maxLength={500} placeholder="Why this address is blocked" /></Field>
        </Modal>)}
    </>
  );
}
