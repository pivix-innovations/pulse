"use client";
import { useEffect, useRef, useState } from "react";
import { API, api, getApiTenant } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Card, EmptyState, ShortId } from "@/components/ui";

interface Item { key: number; at: string; title: string; body: string; replayed?: boolean; notification_id?: string; id?: string; acked?: boolean }

export default function Live() {
  const { tenantId } = useAuth();
  const toast = useToast();
  const [recipient, setRecipient] = useState(() => { try { return localStorage.getItem("pulse.liveRecipient") || ""; } catch { return ""; } });
  const [connected, setConnected] = useState(false);
  const [replay, setReplay] = useState(true);
  const [autoack, setAutoack] = useState(false);
  const [status, setStatus] = useState<{ text: string; cls: string }>({ text: "disconnected", cls: "" });
  const [items, setItems] = useState<Item[]>([]);
  const es = useRef<EventSource | null>(null);
  const autoRef = useRef(autoack);
  useEffect(() => { autoRef.current = autoack; }, [autoack]);

  const disconnect = () => { es.current?.close(); es.current = null; setConnected(false); setStatus({ text: "disconnected", cls: "" }); };
  useEffect(() => disconnect, []); // close on unmount
  useEffect(() => () => { es.current?.close(); es.current = null; setConnected(false); setStatus({ text: "disconnected", cls: "" }); }, [tenantId]); // tenant switch

  const ack = async (it: Item) => {
    try {
      await api(`/notifications/${encodeURIComponent(it.notification_id!)}/ack`, { method: "POST", body: { attempt_id: it.id } });
      setItems(xs => xs.map(x => x.key === it.key ? { ...x, acked: true } : x));
    } catch (err) { toast((err as Error).message, "err"); }
  };

  const connect = () => {
    const r = recipient.trim();
    if (!r) return;
    try { localStorage.setItem("pulse.liveRecipient", r); } catch { /* ignore */ }
    const url = `${API}/sse/stream?recipient=${encodeURIComponent(r)}&tenant=${encodeURIComponent(getApiTenant() ?? "")}${replay ? "&replay=1" : ""}`;
    const s = new EventSource(url);
    es.current = s;
    setConnected(true);
    setStatus({ text: "connecting…", cls: "amber" });
    s.onopen = () => setStatus({ text: "live", cls: "green" });
    s.onerror = () => { if (es.current) setStatus({ text: "reconnecting…", cls: "amber" }); };
    s.addEventListener("notification", ev => {
      let data: any = {};
      try { data = JSON.parse((ev as MessageEvent).data); } catch { /* ignore */ }
      const it: Item = { key: Date.now() + Math.random(), at: new Date().toLocaleTimeString(), title: data.title || "Notification", body: data.body || "",
        replayed: data.replayed, notification_id: data.notification_id, id: data.id };
      setItems(xs => [it, ...xs].slice(0, 100));
      if (autoRef.current && it.id && it.notification_id) ack(it);
    });
  };

  return (
    <>
      <Card style={{ marginBottom: 20 }}>
        <form className="toolbar" onSubmit={e => { e.preventDefault(); if (es.current) disconnect(); else connect(); }}>
          <input value={recipient} onChange={e => setRecipient(e.target.value)} placeholder="Recipient external ref (e.g. user-123)" required spellCheck={false} style={{ flex: 1, minWidth: 220 }} aria-label="Recipient" />
          <label className="check" style={{ margin: 0 }}><input type="checkbox" checked={replay} onChange={e => setReplay(e.target.checked)} /> Replay unconfirmed</label>
          <label className="check" style={{ margin: 0 }}><input type="checkbox" checked={autoack} onChange={e => setAutoack(e.target.checked)} /> Auto-ack</label>
          <button className="btn primary" type="submit">{connected ? "Disconnect" : "Connect"}</button>
        </form>
      </Card>
      <Card title="Incoming notifications" actions={<span className={`badge plain ${status.cls}`}>{status.text}</span>} flush>
        {items.length === 0 ? <EmptyState title="Not connected" hint="Connect as a recipient to watch in-app notifications arrive in real time." /> : (
          <div className="feed">{items.map(it => (
            <div className="feed-item" key={it.key}>
              <div className="feed-icon"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></svg></div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="feed-title">{it.title}</div>
                <div className="feed-body">{it.body}</div>
                <div className="feed-meta">
                  <span>{it.at}</span>
                  {it.replayed && <span className="badge amber plain">replayed</span>}
                  {it.notification_id && <ShortId id={it.notification_id} />}
                  {it.id && it.notification_id && (it.acked ? <span className="badge green plain">delivered</span> : <button className="btn sm" onClick={() => ack(it)}>Ack delivered</button>)}
                </div>
              </div>
            </div>))}
          </div>
        )}
      </Card>
    </>
  );
}
