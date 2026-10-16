"use client";
import { useState } from "react";
import { api, qs } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import { Badge, Card, EmptyState, ShortId } from "@/components/ui";

interface Msg { id: string; notification_id: string; title: string | null; body: string; is_read: boolean; is_archived: boolean; created_at?: string }
interface Page { items: Msg[]; next_cursor: string | null; unread_count: number }
const STATES = ["active", "unread", "read", "archived", "all"];

export default function Inbox() {
  const [draft, setDraft] = useState("");
  const [recipient, setRecipient] = useState("");
  const [state, setState] = useState("active");
  const [cursors, setCursors] = useState<string[]>([]); // stack of cursors; last = current page
  const cursor = cursors[cursors.length - 1];
  const q = useTq<Page>(["inbox", recipient, state, cursor ?? ""],
    () => api(`/inbox${qs({ recipient, state, limit: 20, cursor })}`), { enabled: !!recipient });
  const act = useAct(({ id, verb }: { id: string; verb: string }) => api(`/inbox/${id}/${verb}`, { method: "POST" }) , { invalidate: ["inbox"] });
  const readAll = useAct(() => api(`/inbox/read-all${qs({ recipient })}`, { method: "POST" }), { ok: "Marked all as read", invalidate: ["inbox"] });
  const d = q.data;

  return (
    <>
      <Card style={{ marginBottom: 20 }}>
        <form className="toolbar" onSubmit={e => { e.preventDefault(); setRecipient(draft.trim()); setCursors([]); }}>
          <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Recipient external ref (e.g. user-123)" required spellCheck={false} style={{ flex: 1, minWidth: 220 }} aria-label="Recipient" />
          <select value={state} onChange={e => { setState(e.target.value); setCursors([]); }} style={{ width: "auto" }} aria-label="State">
            {STATES.map(s => <option key={s}>{s}</option>)}
          </select>
          <button className="btn primary" type="submit">Load inbox</button>
        </form>
      </Card>
      <Card title={<>Messages {d && <span className="badge blue plain" style={{ marginLeft: 8 }}>{d.unread_count} unread</span>}</>}
        actions={d && d.unread_count > 0 && <button className="btn sm" onClick={() => readAll.mutate(undefined)}>Mark all read</button>} flush>
        {!recipient ? <EmptyState title="Look up a recipient" hint="The inbox is the recipient's persistent in-app message list." />
          : q.isLoading ? <EmptyState title="Loading…" />
          : q.isError ? <EmptyState title="Couldn't load inbox" hint={(q.error as Error).message} />
          : d!.items.length === 0 ? <EmptyState title="Nothing here" hint="No messages in this state." />
          : (
            <>
              {d!.items.map(m => (
                <div className="feed-item" key={m.id} style={m.is_read ? { opacity: 0.75 } : undefined}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="feed-title">{m.title || "Notification"} {!m.is_read && <Badge status="unread" color="blue" plain />} {m.is_archived && <Badge status="archived" plain />}</div>
                    <div className="feed-body">{m.body}</div>
                    <div className="feed-meta"><span>{fmtDate(m.created_at)}</span><ShortId id={m.notification_id} /></div>
                  </div>
                  <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                    <button className="btn sm" onClick={() => act.mutate({ id: m.id, verb: m.is_read ? "unread" : "read" })}>{m.is_read ? "Mark unread" : "Mark read"}</button>
                    <button className="btn sm" onClick={() => act.mutate({ id: m.id, verb: m.is_archived ? "unarchive" : "archive" })}>{m.is_archived ? "Unarchive" : "Archive"}</button>
                  </div>
                </div>
              ))}
              <div className="card-body toolbar" style={{ justifyContent: "space-between" }}>
                <button className="btn sm" disabled={cursors.length === 0} onClick={() => setCursors(c => c.slice(0, -1))}>Newest</button>
                <button className="btn sm" disabled={!d!.next_cursor} onClick={() => setCursors(c => [...c, d!.next_cursor!])}>Older</button>
              </div>
            </>
          )}
      </Card>
    </>
  );
}
