"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { useRouter, usePathname } from "next/navigation";
import { api, setApiTenant, setUnauthorizedHandler } from "./api";
import type { Me, Membership } from "./types";
import { ToastProvider } from "@/components/toast";

interface AuthCtx {
  me: Me | null;
  loading: boolean;
  tenantId: string | null;
  setTenantId: (id: string) => void;
  membership: Membership | null;
  isTenantAdmin: boolean;
  isPlatformAdmin: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}
const Ctx = createContext<AuthCtx | null>(null);
export const useAuth = () => { const c = useContext(Ctx); if (!c) throw new Error("no AuthProvider"); return c; };

const TENANT_KEY = "pulse.tenantId";

function AuthInner({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [tenantId, setTenant] = useState<string | null>(null);
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();

  const adopt = useCallback((m: Me | null) => {
    setMe(m);
    if (!m) { setTenant(null); setApiTenant(null); return; }
    let stored: string | null = null;
    try { stored = localStorage.getItem(TENANT_KEY); } catch { /* ignore */ }
    const id = m.memberships.some(x => x.tenant_id === stored) ? stored : (m.memberships[0]?.tenant_id ?? null);
    setApiTenant(id); setTenant(id);
  }, []);

  useEffect(() => {
    let alive = true;
    api<Me>("/auth/me").then(m => alive && adopt(m)).catch(() => alive && adopt(null)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [adopt]);

  useEffect(() => {
    setUnauthorizedHandler(() => { adopt(null); qc.clear(); router.replace("/login?expired=1"); });
    return () => setUnauthorizedHandler(null);
  }, [adopt, qc, router]);

  useEffect(() => {
    if (loading) return;
    if (!me && pathname !== "/login") router.replace("/login");
  }, [loading, me, pathname, router]);

  const value = useMemo<AuthCtx>(() => {
    const membership = me?.memberships.find(m => m.tenant_id === tenantId) ?? null;
    return {
      me, loading, tenantId, membership,
      isPlatformAdmin: !!me?.is_platform_admin,
      isTenantAdmin: !!me?.is_platform_admin || membership?.role === "admin",
      setTenantId: id => {
        setApiTenant(id); setTenant(id); qc.clear();
        try { localStorage.setItem(TENANT_KEY, id); } catch { /* ignore */ }
      },
      login: async (email, password) => {
        const m = await api<Me>("/auth/login", { method: "POST", body: { email, password } });
        adopt(m); qc.clear();
      },
      logout: async () => {
        try { await api("/auth/logout", { method: "POST" }); } catch { /* already gone */ }
        adopt(null); qc.clear(); router.replace("/login");
      },
      refresh: async () => { adopt(await api<Me>("/auth/me")); },
    };
  }, [me, loading, tenantId, adopt, qc, router]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 5_000, retry: false, refetchOnWindowFocus: false } },
  }));
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider><AuthInner>{children}</AuthInner></ToastProvider>
    </QueryClientProvider>
  );
}
