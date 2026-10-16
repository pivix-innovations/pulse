"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAct } from "@/lib/hooks";
import { useToast } from "@/components/toast";
import { Badge, Card, Field, ShortId } from "@/components/ui";
import { icons } from "@/components/icons";

export default function SendEvent() {
  const toast = useToast();
  const [result, setResult] = useState<{ notification_id: string } | null>(null);
  const send = useAct((body: Record<string, unknown>) => api<{ notification_id: string }>("/events", { method: "POST", body }),
    { ok: "Event accepted", onDone: setResult });

  return (
    <>
      <div style={{ maxWidth: 680 }}>
        <Card title="Publish event" sub="Accepted events are resolved through the matching workflow and fanned out per channel.">
          <form onSubmit={e => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            let payload: unknown;
            try { payload = JSON.parse(String(fd.get("payload") || "{}")); } catch { toast("Payload is not valid JSON", "err"); return; }
            send.mutate({
              event_type: fd.get("event_type"), source: fd.get("source"), payload,
              ...(fd.get("idempotency_key") ? { idempotency_key: fd.get("idempotency_key") } : {}),
              ...(fd.get("scheduled_at") ? { scheduled_at: new Date(String(fd.get("scheduled_at"))).toISOString() } : {}),
            });
          }}>
            <div className="row">
              <Field label="Event type"><input name="event_type" required placeholder="user.signed_up" spellCheck={false} /></Field>
              <Field label="Source"><input name="source" required placeholder="quotient" spellCheck={false} /></Field>
            </div>
            <Field label="Payload (JSON)">
              <textarea name="payload" rows={8} spellCheck={false} defaultValue={'{\n  "recipients": [\n    { "external_ref": "user-123", "email": "ada@example.com" }\n  ],\n  "name": "Ada"\n}'} />
            </Field>
            <div className="row">
              <Field label="Idempotency key (optional)"><input name="idempotency_key" placeholder="unique-key" spellCheck={false} /></Field>
              <Field label="Schedule for (optional)"><input name="scheduled_at" type="datetime-local" /></Field>
            </div>
            <button className="btn primary" type="submit" disabled={send.isPending}><icons.send /> Send event</button>
          </form>
        </Card>
      </div>
      {result && (
        <div style={{ maxWidth: 680, marginTop: 20 }}>
          <Card>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
              <div>
                <div style={{ fontWeight: 600 }}>Notification created</div>
                <div className="muted" style={{ fontSize: 12.5 }}><ShortId id={result.notification_id} /> — <Badge status="accepted" /></div>
              </div>
              <Link className="btn" href={`/notifications?id=${result.notification_id}`}>Inspect delivery →</Link>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
