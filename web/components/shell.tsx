"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { routes } from "@/lib/routes";
import { Modal, Field } from "./ui";
import { useToast } from "./toast";

function useHealth() {
  const q = useQuery({
    queryKey: ["health"],
    queryFn: async () => (await fetch("/health", { credentials: "same-origin" })).ok,
    refetchInterval: 30_000,
  });
  return q.isError ? false : q.data;
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { me, loading, tenantId, setTenantId, isTenantAdmin, isPlatformAdmin, membership, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const toast = useToast();
  const [menu, setMenu] = useState(false);
  const [pw, setPw] = useState(false);
  const health = useHealth();
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);

  const visible = routes.filter(r =>
    !r.access || (r.access === "tenantAdmin" ? isTenantAdmin : isPlatformAdmin));
  const current = routes.find(r => pathname === r.path || pathname.startsWith(r.path + "/"));

  useEffect(() => {
    if (!loading && me && current && !visible.includes(current)) router.replace("/overview");
  }, [loading, me, current, visible, router]);

  if (loading || !me) return null;

  const sections: Record<string, typeof routes> = {};
  for (const r of visible) (sections[r.section] ??= []).push(r);
  const name = me.full_name || me.email;

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12h4l3-8 4 16 3-8h6" /></svg>
          </div>
          <div><div className="brand-name">Pulse</div><div className="brand-sub">Notification Console</div></div>
        </div>
        <nav className="nav" aria-label="Main">
          {Object.entries(sections).map(([sec, items]) => (
            <div key={sec}>
              <div className="nav-section">{sec}</div>
              {items.map(r => (
                <Link key={r.path} href={r.path} className={current === r ? "active" : ""} aria-current={current === r ? "page" : undefined}>
                  <r.icon /><span>{r.title}</span>
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className={`conn-dot ${health === undefined ? "" : health ? "ok" : "bad"}`} />
          <span>{health === undefined ? "Checking…" : health ? "API connected" : "API unreachable"}</span>
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <div>
            <div className="page-title">{current?.title ?? "Pulse"}</div>
            <div className="page-desc">{current?.desc}</div>
          </div>
          <div className="topbar-right">
            <select className="tenant-switch" title="Acting tenant" value={tenantId ?? ""} onChange={e => setTenantId(e.target.value)} aria-label="Acting tenant">
              {me.memberships.length === 0 && <option value="">No tenants</option>}
              {me.memberships.map(m => <option key={m.tenant_id} value={m.tenant_id}>{m.tenant_name}</option>)}
            </select>
            <div style={{ position: "relative" }} ref={menuRef}>
              <button className="user-chip" onClick={() => setMenu(m => !m)} aria-haspopup="menu" aria-expanded={menu}>
                <div className="avatar">{name.trim().slice(0, 1).toUpperCase()}</div>
                <span className="user-name">{name}</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
              </button>
              {menu && (
                <div className="menu" role="menu">
                  <div className="menu-head">
                    <div style={{ fontWeight: 600 }}>{me.email}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{isPlatformAdmin ? "Platform admin" : membership?.role === "admin" ? "Tenant admin" : "Member"}</div>
                  </div>
                  <button role="menuitem" onClick={() => { setMenu(false); setPw(true); }}>Change password</button>
                  <button role="menuitem" onClick={logout}>Sign out</button>
                </div>
              )}
            </div>
          </div>
        </div>
        <main className="content">{children}</main>
      </div>

      {pw && (
        <Modal title="Change password" submitLabel="Update password" onClose={() => setPw(false)}
          onSubmit={async fd => {
            await api("/auth/password", { method: "POST", body: { current_password: fd.get("current"), new_password: fd.get("next") } });
            toast("Password updated", "ok");
          }}>
          <Field label="Current password"><input name="current" type="password" required autoComplete="current-password" /></Field>
          <Field label="New password"><input name="next" type="password" required minLength={8} autoComplete="new-password" /></Field>
        </Modal>
      )}
    </div>
  );
}
