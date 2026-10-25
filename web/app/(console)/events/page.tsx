"use client";
import { Fragment, useState } from "react";
import { apiPage, qs } from "@/lib/api";
import { useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import { Card, EmptyState, Pager, ShortId, rowProps } from "@/components/ui";

interface EventRow {
  id: string; event_type: string; source: string; payload: unknown;
  idempotency_key?: string | null; occurred_at?: string; received_at?: string;
}
const PAGE = 25;

export default function Events() {
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const q = useTq(["events", offset], () => apiPage<EventRow>(`/events${qs({ limit: PAGE, offset })}`), { refetchInterval: 15_000 });
  const items = q.data?.data ?? [];
  return (
    <Card title="Events" sub="Newest first. Click a row to see the payload." flush
      actions={<button className="btn" onClick={() => q.refetch()}>Refresh</button>}>
      {q.isLoading ? <EmptyState title="Loading…" />
        : q.isError ? <EmptyState title="Couldn't load events" hint={(q.error as Error).message} />
        : items.length === 0 ? <EmptyState title="No events" hint="Events published to the pipeline appear here." />
        : (
          <>
            <table>
              <thead><tr><th>Received</th><th>Type</th><th>Source</th><th>Idempotency key</th><th>Event</th></tr></thead>
              <tbody>{items.map(e => (
                <Fragment key={e.id}>
                  <tr {...rowProps(() => setOpenId(openId === e.id ? null : e.id))}>
                    <td className="muted">{fmtDate(e.received_at)}</td><td className="mono">{e.event_type}</td>
                    <td>{e.source}</td><td className="mono muted">{e.idempotency_key || "—"}</td><td><ShortId id={e.id} /></td>
                  </tr>
                  {openId === e.id && (
                    <tr><td colSpan={5}><pre className="json">{JSON.stringify(e.payload, null, 2)}</pre></td></tr>
                  )}
                </Fragment>))}</tbody>
            </table>
            <Pager offset={offset} count={items.length} total={q.data?.total} page={PAGE} onChange={setOffset} />
          </>
        )}
    </Card>
  );
}
