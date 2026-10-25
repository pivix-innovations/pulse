// Same-origin client for the Pulse API (proxied by next.config.ts rewrites).
export const API = "/api/v1";

let tenantId: string | null = null;
export const setApiTenant = (id: string | null) => { tenantId = id; };
export const getApiTenant = () => tenantId;

let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: (() => void) | null) => { onUnauthorized = fn; };

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

type Opts = Omit<RequestInit, "body"> & { body?: unknown };

async function request(path: string, opts: Opts = {}) {
  const { body, headers, ...rest } = opts;
  const res = await fetch(API + path, {
    credentials: "same-origin",
    ...rest,
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: {
      ...(tenantId ? { "X-Tenant-Id": tenantId } : {}),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(headers as Record<string, string> | undefined),
    },
  });
  if (res.status === 401 && !path.startsWith("/auth/")) {
    onUnauthorized?.();
    throw new ApiError("Your session has expired — sign in again.", 401);
  }
  let data: unknown = null;
  if (res.status !== 204) { try { data = await res.json(); } catch { /* non-JSON body */ } }
  if (!res.ok) {
    const d = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(
      d ? (typeof d === "string" ? d : JSON.stringify(d)) : `${res.status} ${res.statusText}`,
      res.status,
    );
  }
  return { data, total: Number(res.headers.get("X-Total-Count") || 0) };
}

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  return (await request(path, opts)).data as T;
}

/** For paged lists: also returns the X-Total-Count header. */
export async function apiPage<T = any>(path: string, opts: Opts = {}): Promise<{ data: T[]; total: number }> {
  const r = await request(path, opts);
  return { data: (r.data ?? []) as T[], total: r.total };
}

export const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};
