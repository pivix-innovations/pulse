"use client";
import Link from "next/link";
import { useState } from "react";
import { apiPage, api, qs } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import { Card, ChannelBadge, EmptyState, Pager, ShortId } from "@/components/ui";

interface DeadLetter { id: string; created_at: string; channel: string; notification_id: string; attempt_id: string; reason: string }
const PAGE = 20;

export default function DeadLetters() {
  const [offset, setOffset] = useState(0);
  const q = useTq(["dead-letters", offset], () => apiPage<DeadLetter>(`/dead-letters${qs({ limit: PAGE, offset })}`), { refetchInterval: 20_000 });
  const retry = useAct((id: string) => api(`/dead-letters/${id}/retry`, { method: "POST" }),
    { ok: "Re-queued for delivery", invalidate: ["dead-letters"] });
  const items = q.data?.data ?? [];

  return (
    <Card title="Dead letters" sub="Deliveries that exhausted retries or could not be confirmed or escalated."
      actions={<button className="btn" onClick={() => q.refetch()}>Refresh</button>} flush>
      {q.isLoading ? <EmptyState title="Loading…" />
        : q.isError ? <EmptyState title="Couldn't load dead letters" hint={(q.error as Error).message} />
        : items.length === 0 && offset === 0 ? <EmptyState title="No dead letters" hint="Every delivery is either in flight or accounted for." />
        : (
          <>
            <table>
              <thead><tr><th>When</th><th>Channel</th><th>Notification</th><th>Attempt</th><th>Reason</th><th /></tr></thead>
              <tbody>{items.map(d => (
                <tr key={d.id}>
                  <td className="muted">{fmtDate(d.created_at)}</td>
                  <td><ChannelBadge channel={d.channel} /></td>
                  <td><Link href={`/notifications?id=${d.notification_id}`}><ShortId id={d.notification_id} /></Link></td>
                  <td><ShortId id={d.attempt_id} /></td>
                  <td style={{ maxWidth: 360, whiteSpace: "normal" }}>{d.reason}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn sm" disabled={retry.isPending} onClick={() => retry.mutate(d.id)}>Retry</button>
                  </td>
                </tr>))}
              </tbody>
            </table>
            <Pager offset={offset} count={items.length} total={q.data?.total} page={PAGE} onChange={setOffset} />
          </>
        )}
    </Card>
  );
}
