"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAct } from "@/lib/hooks";
import { Card, Field, ShortId } from "@/components/ui";

export default function Devices() {
  const [res, setRes] = useState<{ id: string; recipient_id: string } | null>(null);
  const reg = useAct((body: unknown) => api<{ id: string; recipient_id: string }>("/devices", { method: "POST", body }), { ok: "Device registered", onDone: setRes });
  return (
    <div style={{ maxWidth: 640 }}>
      <Card title="Register device token" sub="Push notifications target the recipient's registered device tokens.">
        <form onSubmit={e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          reg.mutate({ external_ref: fd.get("external_ref"), platform: fd.get("platform"), token: fd.get("token") });
        }}>
          <Field label="Recipient external ref"><input name="external_ref" required placeholder="user-123" spellCheck={false} /></Field>
          <div className="row"><Field label="Platform"><select name="platform"><option value="android">Android</option><option value="ios">iOS</option><option value="web">Web</option></select></Field></div>
          <Field label="Device token"><textarea name="token" rows={3} required spellCheck={false} placeholder="FCM registration token" /></Field>
          <button className="btn primary" type="submit" disabled={reg.isPending}>Register device</button>
        </form>
        {res && <div className="muted" style={{ fontSize: 12.5, marginTop: 14 }}>Device <ShortId id={res.id} /> registered for recipient <ShortId id={res.recipient_id} /></div>}
      </Card>
    </div>
  );
}
