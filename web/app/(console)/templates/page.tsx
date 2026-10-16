"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAct, useTq } from "@/lib/hooks";
import { chLabel, fmtDate } from "@/lib/format";
import { useToast } from "@/components/toast";
import { ActiveBadge, Badge, Card, ChannelBadge, EmptyState, Field, Modal, ShortId, errMsg } from "@/components/ui";

interface Template {
  id: string; name: string; channel: string; format: string; locale: string; subject?: string | null; body: string;
  is_active: boolean; published_version: number | null; has_unpublished_changes?: boolean; updated_at?: string; created_at?: string;
}
interface Version { version: number; created_at: string; note?: string | null; is_published: boolean; subject?: string | null; body: string }
interface Usage { version: number; messages: number; last_used_at?: string }
interface Diff { changed: boolean; subject_diff: string[]; body_diff: string[] }
interface Preview { subject?: string | null; text: string; html?: string | null; version: string | number }

function formToBody(fd: FormData) {
  return {
    name: fd.get("name"), channel: fd.get("channel"), format: fd.get("format"),
    locale: fd.get("locale") || "en", subject: fd.get("subject") || null,
    body: fd.get("body"), is_active: fd.get("is_active") === "on",
    ...(fd.has("publish") ? { publish: fd.get("publish") === "on" } : {}),
  };
}

export default function Templates() {
  const toast = useToast();
  const q = useTq<Template[]>(["templates"], () => api("/templates"));
  const [modal, setModal] = useState<null | { kind: "new" } | { kind: "edit" | "preview" | "history"; t: Template }>(null);
  const del = useAct((id: string) => api(`/templates/${id}`, { method: "DELETE" }), { ok: "Template deleted", invalidate: ["templates"] });
  const publish = useAct(({ id, note }: { id: string; note: string | null }) => api(`/templates/${id}/publish`, { method: "POST", body: { note } }),
    { ok: "Published", invalidate: ["templates"] });
  const items = q.data ?? [];
  const close = () => setModal(null);

  return (
    <>
      <Card title="Templates" actions={<button className="btn primary" onClick={() => setModal({ kind: "new" })}>+ New template</button>} flush>
        {q.isLoading ? <EmptyState title="Loading…" />
          : q.isError ? <EmptyState title="Couldn't load templates" hint={errMsg(q.error)} />
          : items.length === 0 ? <EmptyState title="No templates yet" hint="Create a template so workflows have something to render." />
          : (
            <table>
              <thead><tr><th>Name</th><th>Channel</th><th>Locale</th><th>Live version</th><th>Status</th><th>Updated</th><th /></tr></thead>
              <tbody>{items.map(t => (
                <tr key={t.id}>
                  <td style={{ fontWeight: 550 }}>{t.name}<div className="muted" style={{ fontWeight: 400 }}><ShortId id={t.id} /></div></td>
                  <td><ChannelBadge channel={t.channel} /></td><td className="mono">{t.locale}</td>
                  <td>{t.published_version ? <span className="mono">v{t.published_version}</span> : <span className="muted">none</span>}
                    {t.has_unpublished_changes && <div style={{ fontSize: 11, color: "var(--amber)" }}>{t.published_version ? "draft has changes" : "draft only"}</div>}</td>
                  <td><ActiveBadge active={t.is_active} /></td>
                  <td className="muted">{fmtDate(t.updated_at || t.created_at)}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {t.has_unpublished_changes && <button className="btn sm primary" onClick={() => {
                      const next = (t.published_version || 0) + 1;
                      const note = window.prompt(`Publish the draft of "${t.name}" as v${next}?\nOptional note:`, "");
                      if (note !== null) publish.mutate({ id: t.id, note: note || null }, { onSuccess: () => toast(`Published v${next}`, "ok") });
                    }}>Publish</button>}{" "}
                    <button className="btn sm" onClick={() => setModal({ kind: "preview", t })}>Preview</button>{" "}
                    <button className="btn sm" onClick={() => setModal({ kind: "history", t })}>History</button>{" "}
                    <button className="btn sm" onClick={() => setModal({ kind: "edit", t })}>Edit</button>{" "}
                    <button className="btn sm danger" onClick={() => window.confirm("Delete this template?") && del.mutate(t.id)}>Delete</button>
                  </td>
                </tr>))}</tbody>
            </table>
          )}
      </Card>
      {modal?.kind === "new" && <TemplateModal onClose={close} />}
      {modal?.kind === "edit" && <TemplateModal t={modal.t} onClose={close} />}
      {modal?.kind === "preview" && <PreviewModal t={modal.t} onClose={close} />}
      {modal?.kind === "history" && <HistoryModal t={modal.t} onClose={close} />}
    </>
  );
}

function TemplateModal({ t, onClose }: { t?: Template; onClose: () => void }) {
  const creating = !t;
  const toast = useToast();
  const act = useAct((body: unknown) => t ? api(`/templates/${t.id}`, { method: "PUT", body }) : api("/templates", { method: "POST", body }),
    { invalidate: ["templates"] });
  return (
    <Modal title={creating ? "New template" : "Edit template"} submitLabel={creating ? "Create" : "Save"} onClose={onClose}
      onSubmit={async fd => { await act.mutateAsync(formToBody(fd)); toast(creating ? "Template created" : "Template updated", "ok"); }}>
      <div className="chain-note" style={{ marginBottom: 12 }}>{creating
        ? <>Workflows send the <b>published</b> version of a template. Save as a draft to work on it, or publish straight away.</>
        : <>Saving changes the <b>draft</b>. Workflows keep sending the published version (<b>{t!.published_version ? "v" + t!.published_version : "none yet"}</b>) until you publish.</>}</div>
      <Field label="Name"><input name="name" required defaultValue={t?.name} placeholder="welcome-email" /></Field>
      <div className="row">
        <Field label="Channel"><select name="channel" defaultValue={t?.channel}>{["email", "push", "sms", "in_app"].map(c => <option key={c} value={c}>{chLabel(c)}</option>)}</select></Field>
        <Field label="Format"><select name="format" defaultValue={t?.format || "markdown"}>{["markdown", "text", "html"].map(f => <option key={f}>{f}</option>)}</select></Field>
        <Field label="Locale"><input name="locale" defaultValue={t?.locale || "en"} /></Field>
      </div>
      <Field label="Subject (email/push)"><input name="subject" defaultValue={t?.subject ?? ""} placeholder="Welcome, {{ name }}!" /></Field>
      <Field label="Body (Jinja2)"><textarea name="body" rows={8} required defaultValue={t?.body} placeholder="Hi {{ name }}, …" /></Field>
      <label className="check"><input type="checkbox" name="is_active" defaultChecked={t?.is_active !== false} /> Active</label>
      {creating && <label className="check" style={{ marginTop: 8 }}><input type="checkbox" name="publish" /> Publish immediately</label>}
    </Modal>
  );
}

function sampleDataFor(t: Template) {
  const text = `${t.subject || ""} ${t.body || ""}`;
  const names = new Set<string>();
  for (const m of text.matchAll(/\{\{\s*([a-zA-Z_]\w*)/g)) names.add(m[1]);
  for (const m of text.matchAll(/\{%-?\s*if\s+([a-zA-Z_]\w*)/g)) names.add(m[1]);
  ["recipient", "digest", "loop", "range", "true", "false", "none", "not"].forEach(n => names.delete(n));
  const data = Object.fromEntries([...names].map(n => [n, `sample ${n}`]));
  return JSON.stringify(Object.keys(data).length ? data : { plan: "pro" }, null, 2);
}

function PreviewModal({ t, onClose }: { t: Template; onClose: () => void }) {
  const hasLive = t.published_version != null;
  const [version, setVersion] = useState(hasLive ? "published" : "draft");
  const [locale, setLocale] = useState(t.locale || "en");
  const [data, setData] = useState(sampleDataFor(t));
  const [digest, setDigest] = useState(false);
  const [out, setOut] = useState<{ r?: Preview; err?: string }>({});
  const run = async () => {
    let parsed: unknown;
    try { parsed = JSON.parse(data || "{}"); } catch { setOut({ err: "The sample data is not valid JSON." }); return; }
    try {
      const r = await api<Preview>(`/templates/${t.id}/preview`, { method: "POST", body: { data: parsed, version, recipient: { locale }, ...(digest ? { digest: [parsed, parsed, parsed] } : {}) } });
      setOut({ r });
    } catch (e) { setOut({ err: errMsg(e) }); }
  };
  return (
    <Modal title={`Preview · ${t.name}`} width="720px" onClose={onClose} footer={false}>
      <div className="row">
        <Field label="Version"><select value={version} onChange={e => setVersion(e.target.value)}>
          <option value="draft">Draft (what you are editing)</option>
          {hasLive && <option value="published">Published (v{t.published_version}, what is sent)</option>}</select></Field>
        <Field label="Recipient locale"><input value={locale} onChange={e => setLocale(e.target.value)} /></Field>
      </div>
      <Field label="Sample event data (JSON, the event payload)"><textarea rows={5} spellCheck={false} value={data} onChange={e => setData(e.target.value)} /></Field>
      <label className="check" style={{ marginBottom: 10 }}><input type="checkbox" checked={digest} onChange={e => setDigest(e.target.checked)} /> Simulate a digest of 3 events (uses the data above for each)</label>
      <button type="button" className="btn primary" onClick={run}>Render</button>
      <div style={{ marginTop: 14 }}>
        {out.err && <div className="chain-note" style={{ color: "var(--red)" }}>{out.err}</div>}
        {out.r && (
          <>
            {out.r.subject != null && <><div className="muted" style={{ fontSize: 12 }}>Subject</div><div style={{ fontWeight: 600, marginBottom: 10 }}>{out.r.subject}</div></>}
            <div className="muted" style={{ fontSize: 12 }}>Text</div><pre style={{ whiteSpace: "pre-wrap", margin: "0 0 10px" }}>{out.r.text}</pre>
            {out.r.html && <><div className="muted" style={{ fontSize: 12 }}>HTML</div>
              <iframe sandbox="" title="HTML preview" style={{ width: "100%", height: 180, border: "1px solid var(--border)", borderRadius: 8, background: "#fff" }} srcDoc={out.r.html} /></>}
            <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Rendered {out.r.version === "draft" ? "the draft" : "version " + out.r.version}</div>
          </>
        )}
      </div>
    </Modal>
  );
}

function HistoryModal({ t, onClose }: { t: Template; onClose: () => void }) {
  const toast = useToast();
  const versions = useTq<Version[]>(["template-versions", t.id], () => api(`/templates/${t.id}/versions`));
  const usage = useTq<Usage[]>(["template-usage", t.id], () => api(`/templates/${t.id}/usage`));
  const [view, setView] = useState<{ diff?: { n: number; d: Diff }; body?: string }>({});
  const rollback = useAct((n: number) => api(`/templates/${t.id}/rollback`, { method: "POST", body: { version: n } }),
    { invalidate: ["templates", "template-versions", "template-usage"], onDone: onClose });
  const used = Object.fromEntries((usage.data ?? []).map(u => [u.version, u]));
  const vs = versions.data ?? [];
  const diffLines = (title: string, lines: string[]) => lines.length ? (
    <><div className="muted" style={{ marginTop: 8 }}>{title}</div>
      <pre style={{ margin: "4px 0", whiteSpace: "pre-wrap" }}>{lines.map((l, i) => (
        <div key={i} style={{ color: l.startsWith("+") ? "var(--green)" : l.startsWith("-") ? "var(--red)" : "var(--muted)", background: l.startsWith("+") ? "var(--green-bg)" : l.startsWith("-") ? "var(--red-bg)" : undefined }}>{l || " "}</div>))}</pre></>
  ) : null;
  return (
    <Modal title={`History · ${t.name}`} width="720px" onClose={onClose} footer={false}>
      {versions.isLoading ? <EmptyState title="Loading…" /> : vs.length === 0 ? <EmptyState title="Nothing published yet" hint="Publish the draft to create version 1." /> : (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th>Version</th><th>When</th><th>Note</th><th>Messages sent</th><th /></tr></thead>
            <tbody>{vs.map(v => (
              <tr key={v.version}>
                <td style={{ fontWeight: 600 }}>v{v.version} {v.is_published && <Badge status="live" color="green" />}</td>
                <td className="muted">{fmtDate(v.created_at)}</td><td className="muted">{v.note || "—"}</td>
                <td className="muted" title={used[v.version] ? "Last used " + fmtDate(used[v.version].last_used_at) : ""}>{used[v.version]?.messages ?? 0}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  <button type="button" className="btn sm" onClick={() => setView({ body: (v.subject ? `Subject: ${v.subject}\n\n` : "") + v.body })}>View</button>{" "}
                  {v.version > 1 && <button type="button" className="btn sm" onClick={async () => {
                    try { setView({ diff: { n: v.version, d: await api<Diff>(`/templates/${t.id}/diff?from=${v.version - 1}&to=${v.version}`) } }); }
                    catch (e) { toast(errMsg(e), "err"); }
                  }}>Changes</button>}{" "}
                  {!v.is_published && <button type="button" className="btn sm" onClick={() => window.confirm(`Publish a copy of version ${v.version} as the new live version? Your current draft will be replaced by it.`) && rollback.mutate(v.version, { onSuccess: () => toast(`Rolled back to v${v.version}`, "ok") })}>Roll back to this</button>}
                </td>
              </tr>))}</tbody>
          </table></div>
          {view.diff && <div style={{ marginTop: 12 }}><b>v{view.diff.n - 1} → v{view.diff.n}</b>{view.diff.d.changed
            ? <>{diffLines("Subject", view.diff.d.subject_diff)}{diffLines("Body", view.diff.d.body_diff)}</>
            : <div className="muted">No difference in subject or body.</div>}</div>}
          {view.body && <pre style={{ whiteSpace: "pre-wrap", marginTop: 12 }}>{view.body}</pre>}
        </>
      )}
    </Modal>
  );
}
