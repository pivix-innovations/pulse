"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAct, useTq } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import { useToast } from "@/components/toast";
import { ActiveBadge, Card, EmptyState, Field, Modal, errMsg } from "@/components/ui";
import { RevealBox } from "@/components/reveal";

interface User { id: string; email: string; full_name?: string | null; is_platform_admin: boolean; is_active: boolean; created_at: string }

export default function Users() {
  const { me } = useAuth();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [temp, setTemp] = useState<{ email: string; temporary_password: string } | null>(null);
  const q = useTq<User[]>(["admin-users"], () => api("/admin/users"));
  const patch = useAct(({ id, body }: { id: string; body: unknown }) => api(`/admin/users/${id}`, { method: "PATCH", body }), { ok: "User updated", invalidate: ["admin-users"] });
  const create = useAct((body: unknown) => api<{ email: string; temporary_password?: string }>("/admin/users", { method: "POST", body }), { invalidate: ["admin-users"] });
  const items = q.data ?? [];
  return (
    <>
      <Card title="Users" actions={<button className="btn primary" onClick={() => setAdding(true)}>+ New user</button>} flush>
        {q.isLoading ? <EmptyState title="Loading…" />
          : q.isError ? <EmptyState title="Couldn't load users" hint={errMsg(q.error)} />
          : (
            <table>
              <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Created</th><th /></tr></thead>
              <tbody>{items.map(u => (
                <tr key={u.id}>
                  <td style={{ fontWeight: 550 }}>{u.full_name || u.email}<div className="muted" style={{ fontWeight: 400 }}>{u.email}</div></td>
                  <td>{u.is_platform_admin ? <span className="badge blue plain">platform admin</span> : <span className="badge plain">user</span>}</td>
                  <td><ActiveBadge active={u.is_active} /></td><td className="muted">{fmtDate(u.created_at)}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>{u.id === me?.id ? <span className="muted" style={{ fontSize: 12 }}>you</span> : <>
                    <button className="btn sm" onClick={() => patch.mutate({ id: u.id, body: { is_platform_admin: !u.is_platform_admin } })}>{u.is_platform_admin ? "Demote" : "Make admin"}</button>{" "}
                    <button className={`btn sm ${u.is_active ? "danger" : ""}`} onClick={() => {
                      if (u.is_active && !window.confirm("Disable this user? Their sessions stop working on next request.")) return;
                      patch.mutate({ id: u.id, body: { is_active: !u.is_active } });
                    }}>{u.is_active ? "Disable" : "Enable"}</button></>}</td>
                </tr>))}</tbody>
            </table>)}
      </Card>
      {temp && <RevealBox copy={temp.temporary_password}><b>Account created.</b> Temporary password for {temp.email} — shown only once:
        <div className="mono" style={{ marginTop: 6 }}>{temp.temporary_password}</div></RevealBox>}
      {adding && (
        <Modal title="New user" submitLabel="Create" onClose={() => setAdding(false)} onSubmit={async fd => {
          const res = await create.mutateAsync({ email: fd.get("email"), full_name: fd.get("full_name") || "", is_platform_admin: fd.get("is_platform_admin") === "on", ...(fd.get("password") ? { password: fd.get("password") } : {}) });
          toast("User created", "ok");
          setTemp(res.temporary_password ? { email: res.email, temporary_password: res.temporary_password } : null);
        }}>
          <Field label="Email"><input name="email" type="email" required placeholder="person@company.com" /></Field>
          <Field label="Full name"><input name="full_name" placeholder="Ada Lovelace" /></Field>
          <Field label="Password (leave blank to generate)"><input name="password" type="text" minLength={8} autoComplete="off" /></Field>
          <label className="check"><input type="checkbox" name="is_platform_admin" /> Platform admin</label>
        </Modal>)}
    </>
  );
}
