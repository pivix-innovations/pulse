import type { Provider } from "@/lib/types";

export const providerLogo = (p: Provider) => p.catalog?.logo_url || p.logo_url;

export function ProviderLogo({ p, className = "provider-logo" }: { p: Provider; className?: string }) {
  const url = providerLogo(p);
  // eslint-disable-next-line @next/next/no-img-element
  return url ? <img className={className} src={url} alt="" /> : null;
}

/** Which provider carried an attempt (with logo), plus any failover trail. */
export function ProviderCell({ a, byId }: { a: { provider_id?: string | null; status: string; last_error?: string | null }; byId: Record<string, Provider> }) {
  const p = a.provider_id ? byId[a.provider_id] : null;
  const trail = (a.last_error || "").startsWith("Failed over");
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {p ? <><ProviderLogo p={p} /> {p.label}</>
          : a.provider_id ? <span className="muted">deleted provider</span>
          : a.status === "pending" || a.status === "retrying" ? <span className="muted">—</span>
          : <span className="muted">default</span>}
      </div>
      {trail && <div className="muted" style={{ fontSize: 11 }} title={a.last_error ?? ""}>↪ failed over</div>}
    </>
  );
}
