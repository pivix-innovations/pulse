"use client";
import { useState } from "react";
import { api, qs } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { chLabel } from "@/lib/format";
import { useToast } from "@/components/toast";
import { Card, ChannelBadge, EmptyState, Field, Modal, errMsg } from "@/components/ui";

interface Pref { id: string; channel: string; opted_in: boolean; quiet_hours?: { start: string; end: string; timezone: string } | null }
interface CatPref { id: string; category: string; channel: string; opted_in: boolean }
const CHANNELS = ["email", "push", "sms", "in_app"];

export default function Preferences() {
  const toast = useToast();
  const [draft, setDraft] = useState("");
  const [recipient, setRecipient] = useState("");
  const [modal, setModal] = useState<null | "pref" | "cat">(null);
  const prefs = useTq<Pref[]>(["preferences", recipient], () => api(`/preferences${qs({ recipient })}`), { enabled: !!recipient });
  const cats = useTq<CatPref[]>(["category-preferences", recipient], () => api(`/category-preferences${qs({ recipient })}`), { enabled: !!recipient });
  const delPref = useAct((id: string) => api(`/preferences/${id}`, { method: "DELETE" }), { ok: "Preference deleted", invalidate: ["preferences"] });
  const delCat = useAct((id: string) => api(`/category-preferences/${id}`, { method: "DELETE" }), { ok: "Choice removed", invalidate: ["category-preferences"] });
  const ref = recipient || draft.trim();
  const err = prefs.error || cats.error;

  return (
    <>
      <Card style={{ marginBottom: 20 }}>
        <form className="toolbar" onSubmit={e => { e.preventDefault(); setRecipient(draft.trim()); }}>
          <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Recipient external ref (e.g. user-123)" required spellCheck={false} style={{ flex: 1, minWidth: 220 }} aria-label="Recipient" />
          <button className="btn primary" type="submit">Load preferences</button>
          <button className="btn" type="button" onClick={() => setModal("pref")}>+ Set preference</button>
          <button className="btn" type="button" onClick={() => setModal("cat")}>+ Set category</button>
        </form>
      </Card>
      {!recipient ? <Card><EmptyState title="Look up a recipient" hint="Preferences control channel opt-ins and quiet hours per recipient." /></Card>
        : err ? <Card><EmptyState title="Couldn't load preferences" hint={errMsg(err)} /></Card>
        : (
          <>
            <Card title={<>Preferences for <span className="mono">{recipient}</span></>} flush>
              {!prefs.data?.length ? <EmptyState title="No preferences set" hint="This recipient uses workflow defaults for every channel." /> : (
                <table>
                  <thead><tr><th>Channel</th><th>Opted in</th><th>Quiet hours</th><th /></tr></thead>
                  <tbody>{prefs.data.map(p => (
                    <tr key={p.id}><td><ChannelBadge channel={p.channel} /></td>
                      <td>{p.opted_in ? <span className="badge green plain">opted in</span> : <span className="badge red plain">opted out</span>}</td>
                      <td className="muted">{p.quiet_hours ? `${p.quiet_hours.start} – ${p.quiet_hours.end} (${p.quiet_hours.timezone})` : "—"}</td>
                      <td style={{ textAlign: "right" }}><button className="btn sm danger" onClick={() => window.confirm("Delete this preference?") && delPref.mutate(p.id)}>Delete</button></td></tr>))}</tbody>
                </table>)}
            </Card>
            <Card title="Categories" sub={<>Opt-outs for workflows that declare a <span className="mono">category</span>. Removing a row subscribes them again.</>} flush style={{ marginTop: 16 }}>
              {!cats.data?.length ? <EmptyState title="No category choices" hint="This recipient receives every category." /> : (
                <table>
                  <thead><tr><th>Category</th><th>Channel</th><th>Choice</th><th /></tr></thead>
                  <tbody>{cats.data.map(c => (
                    <tr key={c.id}><td className="mono">{c.category}</td>
                      <td>{c.channel === "all" ? <span className="muted">all channels</span> : <ChannelBadge channel={c.channel} />}</td>
                      <td>{c.opted_in ? <span className="badge green plain">subscribed</span> : <span className="badge red plain">opted out</span>}</td>
                      <td style={{ textAlign: "right" }}><button className="btn sm danger" onClick={() => delCat.mutate(c.id)}>Remove</button></td></tr>))}</tbody>
                </table>)}
            </Card>
          </>
        )}

      {modal === "cat" && (
        <Modal title="Set category preference" onClose={() => setModal(null)} onSubmit={async fd => {
          const body = { external_ref: fd.get("external_ref"), category: fd.get("category"), opted_in: fd.get("opted_in") === "true", ...(fd.get("channel") ? { channel: fd.get("channel") } : {}) };
          await api("/category-preferences", { method: "PUT", body });
          toast("Category preference saved", "ok"); setDraft(String(body.external_ref)); setRecipient(String(body.external_ref));
        }}>
          <Field label="Recipient external ref"><input name="external_ref" required defaultValue={ref} placeholder="user-123" /></Field>
          <Field label="Category"><input name="category" required placeholder="marketing" pattern="[a-z0-9][a-z0-9_-]{0,39}" title="Lowercase letters, digits, - or _" /></Field>
          <div className="row">
            <Field label="Channel"><select name="channel"><option value="">All channels</option>{CHANNELS.map(c => <option key={c} value={c}>{chLabel(c)}</option>)}</select></Field>
            <Field label="Choice"><select name="opted_in"><option value="false">Opted out</option><option value="true">Subscribed</option></select></Field>
          </div>
        </Modal>)}
      {modal === "pref" && (
        <Modal title="Set preference" onClose={() => setModal(null)} onSubmit={async fd => {
          const body: Record<string, unknown> = { external_ref: fd.get("external_ref"), channel: fd.get("channel"), opted_in: fd.get("opted_in") === "true" };
          if (fd.get("use_quiet") === "on") body.quiet_hours = { start: fd.get("qh_start"), end: fd.get("qh_end"), timezone: fd.get("qh_tz") || "UTC" };
          await api("/preferences", { method: "PUT", body });
          toast("Preference saved", "ok"); setDraft(String(body.external_ref)); setRecipient(String(body.external_ref));
        }}>
          <Field label="Recipient external ref"><input name="external_ref" required defaultValue={ref} placeholder="user-123" /></Field>
          <div className="row">
            <Field label="Channel"><select name="channel">{CHANNELS.map(c => <option key={c} value={c}>{chLabel(c)}</option>)}</select></Field>
            <Field label="Opted in"><select name="opted_in"><option value="true">Yes</option><option value="false">No</option></select></Field>
          </div>
          <label className="check" style={{ marginBottom: 10 }}><input type="checkbox" name="use_quiet" /> Set quiet hours</label>
          <div className="row">
            <Field label="Start"><input name="qh_start" type="time" defaultValue="22:00" /></Field>
            <Field label="End"><input name="qh_end" type="time" defaultValue="07:00" /></Field>
            <Field label="Timezone"><input name="qh_tz" defaultValue="UTC" /></Field>
          </div>
        </Modal>)}
    </>
  );
}
