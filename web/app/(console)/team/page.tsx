"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAct, useTq } from "@/lib/hooks";
import { useToast } from "@/components/toast";
import { ActiveBadge, Card, EmptyState, Field, Modal, errMsg } from "@/components/ui";
import { RevealBox } from "@/components/reveal";

interface Member { membership_id: string; user_id: string; email: string; full_name?: string | null; role: string; is_active: boolean }

export default function Team() {
  const { me } = useAuth();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [temp, setTemp] = useState<{ email: string; temporary_password: string } | null>(null);
  const q = useTq<Member[]>(["members"], () => api("/tenant/members"));
  const del = useAct((id: string) => api(`/tenant/members/${id}`, { method: "DELETE" }), { ok: "Member removed", invalidate: ["members"] });
  const add = useAct((body: unknown) => api<{ email: string; temporary_password?: string }>("/tenant/members", { method: "POST", body }), { invalidate: ["members"] });
  const items = q.data ?? [];
  return (
    <>
      <Card title="Members" sub="Admins manage members, API keys, and configuration; members operate the console."
        actions={<button className="btn primary" onClick={() => setAdding(true)}>+ Add member</button>} flush>
        {q.isLoading ? <EmptyState title="Loading…" />
          : q.isError ? <EmptyState title="Couldn't load members" hint={errMsg(q.error)} />
          : items.length === 0 ? <EmptyState title="No members" hint="Add teammates by email — new accounts get a temporary password." />
          : (
            <table>
              <thead><tr><th>User</th><th>Role</th><th>Status</th><th /></tr></thead>
              <tbody>{items.map(m => (
                <tr key={m.membership_id}>
                  <td style={{ fontWeight: 550 }}>{m.full_name || m.email}<div className="muted" style={{ fontWeight: 400 }}>{m.email}</div></td>
                  <td>{m.role === "admin" ? <span className="badge blue plain">admin</span> : <span className="badge plain">member</span>}</td>
                  <td><ActiveBadge active={m.is_active} /></td>
                  <td style={{ textAlign: "right" }}>{m.user_id === me?.id ? <span className="muted" style={{ fontSize: 12 }}>you</span>
                    : <button className="btn sm danger" onClick={() => window.confirm("Remove this member from the tenant?") && del.mutate(m.membership_id)}>Remove</button>}</td>
                </tr>))}</tbody>
            </table>)}
      </Card>
      {temp && <RevealBox copy={temp.temporary_password}><b>New account created.</b> Share this temporary password with {temp.email} — it is shown only once:
        <div className="mono" style={{ marginTop: 6 }}>{temp.temporary_password}</div></RevealBox>}
      {adding && (
        <Modal title="Add member" submitLabel="Add" onClose={() => setAdding(false)} onSubmit={async fd => {
          const res = await add.mutateAsync({ email: fd.get("email"), full_name: fd.get("full_name") || "", role: fd.get("role") });
          toast("Member added", "ok");
          setTemp(res.temporary_password ? { email: res.email, temporary_password: res.temporary_password } : null);
        }}>
          <Field label="Email"><input name="email" type="email" required placeholder="teammate@company.com" /></Field>
          <div className="row">
            <Field label="Full name (new accounts)"><input name="full_name" placeholder="Ada Lovelace" /></Field>
            <Field label="Role"><select name="role"><option value="member">Member</option><option value="admin">Admin</option></select></Field>
          </div>
        </Modal>)}
    </>
  );
}
