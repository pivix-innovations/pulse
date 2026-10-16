"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, apiPage, qs } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { useToast } from "@/components/toast";
import { chLabel, fmtDate } from "@/lib/format";
import type { Provider } from "@/lib/types";
import { Badge, Card, ChannelBadge, EmptyState, Pager, ShortId, rowProps } from "@/components/ui";
import { ProviderCell } from "@/components/provider";

interface Attempt {
  id: string; channel: string; provider_id?: string | null; status: string; attempt_count: number;
  sent_at?: string; delivered_at?: string; last_error?: string | null; awaiting_confirmation?: boolean;
}
interface Notification {
  id: string; status: string; created_at: string; event_id: string; workflow_id: string; attempts: Attempt[];
  digest?: { is_anchor: boolean; item_count: number; status: string; flush_at: string; anchor_notification_id: string } | null;
  steps?: { step_index: number; step_id: string; recipient_id: string; status: string; executed_at?: string; run_at?: string; reason?: string; attempt_id?: string }[];
}
interface Summary { id: string; status: string; created_at: string; scheduled_at?: string }
const PAGE = 20;

function Page() {
  const router = useRouter();
  const id = useSearchParams().get("id") ?? "";
  const [draft, setDraft] = useState(id);
  const [status, setStatus] = useState("");
  const [channel, setChannel] = useState("");
  const [eventType, setEventType] = useState("");
  const [person, setPerson] = useState("");
  const [recipient, setRecipient] = useState("");   // applied on submit, so typing doesn't query per keystroke
  const [offset, setOffset] = useState(0);
  const filtered = !!(status || channel || eventType.trim() || recipient);
  const recent = useTq(["notifications", status, channel, eventType.trim(), recipient, offset], () =>
    apiPage<Summary>(`/notifications${qs({ limit: PAGE, offset, status, channel, event_type: eventType.trim(), recipient })}`), { refetchInterval: 15_000 });
  const items = recent.data?.data ?? [];
  const open = (v: string) => { setDraft(v); router.push(`/notifications?id=${encodeURIComponent(v.trim())}`); window.scrollTo({ top: 0, behavior: "smooth" }); };

  return (
    <>
      <Card style={{ marginBottom: 20 }}>
        <form className="toolbar" onSubmit={e => { e.preventDefault(); if (draft.trim()) open(draft); }}>
          <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Notification ID (UUID)" required spellCheck={false}
            style={{ flex: 1, minWidth: 260, fontFamily: "ui-monospace,monospace", fontSize: 12.5 }} aria-label="Notification ID" />
          <button className="btn primary" type="submit">Look up</button>
        </form>
      </Card>

      {id && <Detail id={id} />}

      <Card title="Search notifications" sub="Find notifications by who they went to, what triggered them, and how they were sent." style={{ marginBottom: 20 }}>
        <form className="toolbar" onSubmit={e => { e.preventDefault(); setRecipient(person.trim()); setOffset(0); }}>
          <input value={person} onChange={e => setPerson(e.target.value)} placeholder="Recipient (e.g. user-123)" spellCheck={false} style={{ flex: 1, minWidth: 160 }} aria-label="Recipient" />
          <input value={eventType} onChange={e => { setEventType(e.target.value); setOffset(0); }} placeholder="Event type (e.g. user.signed_up)" spellCheck={false} style={{ flex: 1, minWidth: 180 }} aria-label="Event type" />
          <select value={channel} onChange={e => { setChannel(e.target.value); setOffset(0); }} style={{ width: "auto" }} aria-label="Channel filter">
            <option value="">Any channel</option>{["email", "push", "sms", "in_app"].map(c => <option key={c} value={c}>{chLabel(c)}</option>)}
          </select>
          <select value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }} style={{ width: "auto" }} aria-label="Status filter">
            <option value="">All statuses</option>
            {["created", "processing", "dispatched", "completed", "failed"].map(s => <option key={s}>{s}</option>)}
          </select>
          <button className="btn primary" type="submit">Search</button>
          {filtered && <button className="btn" type="button" onClick={() => { setStatus(""); setChannel(""); setEventType(""); setPerson(""); setRecipient(""); setOffset(0); }}>Clear</button>}
        </form>
      </Card>

      <Card title={filtered ? "Matching notifications" : "Recent notifications"} flush>
        {recent.isLoading ? <EmptyState title="Loading…" />
          : recent.isError ? <EmptyState title="Couldn't load notifications" hint={(recent.error as Error).message} />
          : items.length === 0 ? <EmptyState title="No notifications" hint={filtered ? "Nothing matches these filters." : "Send an event and it will show up here."} />
          : (
            <>
              <table>
                <thead><tr><th>Notification</th><th>Status</th><th>Created</th><th>Scheduled</th></tr></thead>
                <tbody>{items.map(n => (
                  <tr key={n.id} {...rowProps(() => open(n.id))}>
                    <td><ShortId id={n.id} /></td><td><Badge status={n.status} /></td>
                    <td className="muted">{fmtDate(n.created_at)}</td><td className="muted">{fmtDate(n.scheduled_at)}</td>
                  </tr>))}</tbody>
              </table>
              <Pager offset={offset} count={items.length} total={recent.data?.total} page={PAGE} onChange={setOffset} />
            </>
          )}
      </Card>
    </>
  );
}

function Detail({ id }: { id: string }) {
  const toast = useToast();
  const n = useTq<Notification>(["notification", id], () => api(`/notifications/${encodeURIComponent(id.trim())}`));
  const providers = useTq<Provider[]>(["providers"], () => api<Provider[]>("/providers").catch(() => []));
  const byId = Object.fromEntries((providers.data ?? []).map(p => [p.id, p]));
  const ack = useAct((attempt_id: string) => api(`/notifications/${encodeURIComponent(id.trim())}/ack`, { method: "POST", body: { attempt_id } }),
    { ok: "Attempt acknowledged as delivered", invalidate: ["notification"] });

  const cancel = useAct(() => api<{ cancelled: number }>(`/notifications/${encodeURIComponent(id.trim())}/cancel-sequence`, { method: "POST" }),
    { invalidate: ["notification", "notifications"], onDone: r => toast(r.cancelled ? `Cancelled ${r.cancelled} waiting step${r.cancelled === 1 ? "" : "s"}` : "No steps were waiting", "ok") });

  if (n.isLoading) return null;
  if (n.isError) return <Card style={{ marginBottom: 20 }}><EmptyState title="Lookup failed" hint={(n.error as Error).message} /></Card>;
  const d = n.data!;
  return (
    <div style={{ marginBottom: 20 }}>
      <div className="stat-grid">
        <div className="stat"><div className="stat-label">Status</div><div style={{ marginTop: 8 }}><Badge status={d.status} /></div></div>
        <div className="stat"><div className="stat-label">Created</div><div className="stat-value" style={{ fontSize: 15 }}>{fmtDate(d.created_at)}</div></div>
        <div className="stat"><div className="stat-label">Event</div><div style={{ marginTop: 8 }}><ShortId id={d.event_id} /></div></div>
        <div className="stat"><div className="stat-label">Workflow</div><div style={{ marginTop: 8 }}><ShortId id={d.workflow_id} /></div></div>
      </div>
      <Card title={`Delivery attempts (${d.attempts.length})`} flush>
        {d.attempts.length === 0 ? <EmptyState title="No attempts yet" hint="Fan-out may still be in the outbox — check back shortly." /> : (
          <table>
            <thead><tr><th>Attempt</th><th>Channel</th><th>Provider</th><th>Status</th><th>Tries</th><th>Sent</th><th>Delivered</th><th>Last error</th><th /></tr></thead>
            <tbody>{d.attempts.map(a => (
              <tr key={a.id}>
                <td><ShortId id={a.id} /></td><td><ChannelBadge channel={a.channel} /></td>
                <td><ProviderCell a={a} byId={byId} /></td>
                <td><Badge status={a.status} />{a.awaiting_confirmation && <div className="muted" style={{ fontSize: 11 }}>awaiting receipt</div>}</td>
                <td>{a.attempt_count}</td><td className="muted">{fmtDate(a.sent_at)}</td><td className="muted">{fmtDate(a.delivered_at)}</td>
                <td className="muted" style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={a.last_error ?? ""}>{a.last_error || "—"}</td>
                <td>{a.status === "sent" && <button className="btn sm" onClick={() => ack.mutate(a.id)}>Ack</button>}</td>
              </tr>))}</tbody>
          </table>
        )}
      </Card>
      {d.digest && (
        <Card title="Digest" style={{ marginTop: 20 }}>
          {d.digest.is_anchor
            ? <>This notification carries the digest: {d.digest.item_count} event(s) {d.digest.status === "open"
                ? <>being collected, sent at <b>{fmtDate(d.digest.flush_at)}</b></> : <>sent together at {fmtDate(d.digest.flush_at)}</>}.</>
            : <>This event was folded into the digest carried by <ShortId id={d.digest.anchor_notification_id} /> ({d.digest.status === "open" ? "not sent yet" : "sent"}).</>}
        </Card>
      )}
      {!!d.steps?.length && (
        <Card title={`Sequence steps (${d.steps.length})`} flush style={{ marginTop: 20 }}
          actions={d.steps.some(r => r.status === "scheduled") && (
            <button className="btn sm danger" disabled={cancel.isPending}
              onClick={() => window.confirm("Cancel the steps that have not run yet? Steps that already ran are not affected.") && cancel.mutate()}>Cancel remaining steps</button>)}>
          <table>
            <thead><tr><th>Step</th><th>Recipient</th><th>State</th><th>Runs / ran</th><th>Why</th></tr></thead>
            <tbody>{d.steps.map(r => (
              <tr key={`${r.step_index}-${r.recipient_id}`}>
                <td className="mono">{r.step_index + 1}. {r.step_id}</td><td><ShortId id={r.recipient_id} /></td>
                <td><Badge status={r.status} /></td><td className="muted">{fmtDate(r.executed_at || r.run_at)}</td>
                <td className="muted">{r.reason || (r.attempt_id ? "sent as attempt " + r.attempt_id.slice(0, 8) : "—")}</td>
              </tr>))}</tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

export default function NotificationsPage() {
  return <Suspense><Page /></Suspense>;
}
