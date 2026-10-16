"use client";
import { useState } from "react";
import { api, qs } from "@/lib/api";
import { useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import { Card, EmptyState, Pager, ShortId, errMsg } from "@/components/ui";

interface Entry { id: string; created_at: string; actor_label?: string | null; actor_type: string; actor_id?: string; action: string; resource_id?: string }
const PAGE = 25;
const TYPES = ["workflow", "template", "provider", "api_key", "member", "suppression"];

export default function AuditLog() {
  const [offset, setOffset] = useState(0);
  const [type, setType] = useState("");
  const q = useTq<Entry[]>(["audit-log", type, offset], () => api(`/audit-log${qs({ limit: PAGE, offset, resource_type: type })}`), { refetchInterval: 30_000 });
  const items = q.data ?? [];
  return (
    <Card title="Audit log" sub="Every change to workflows, templates, providers, API keys, team members and suppressions. Request contents are never stored." flush
      actions={<select value={type} onChange={e => { setType(e.target.value); setOffset(0); }} style={{ width: "auto" }} title="Filter by kind of resource" aria-label="Resource type">
        <option value="">All resources</option>{TYPES.map(t => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select>}>
      {q.isLoading ? <EmptyState title="Loading…" />
        : q.isError ? <EmptyState title="Couldn't load the audit log" hint={errMsg(q.error)} />
        : items.length === 0 && offset === 0 ? <EmptyState title="Nothing recorded" hint="Changes made from now on will show up here." />
        : (
          <>
            <table>
              <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Resource</th></tr></thead>
              <tbody>{items.map(e => (
                <tr key={e.id}><td className="muted">{fmtDate(e.created_at)}</td>
                  <td>{e.actor_label ?? <><span className="muted">{e.actor_type.replace("_", " ")}</span> <ShortId id={e.actor_id} /></>}</td>
                  <td><span className="mono">{e.action}</span></td><td><ShortId id={e.resource_id} /></td></tr>))}</tbody>
            </table>
            <Pager offset={offset} count={items.length} page={PAGE} onChange={setOffset} />
          </>
        )}
    </Card>
  );
}
