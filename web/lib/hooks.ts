"use client";
import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useAuth } from "./auth";
import { useToast } from "@/components/toast";

/** useQuery whose cache key is scoped to the acting tenant. */
export function useTq<T>(key: QueryKey, fn: () => Promise<T>, opts: { refetchInterval?: number; enabled?: boolean } = {}) {
  const { tenantId } = useAuth();
  return useQuery({
    queryKey: [tenantId, ...key], queryFn: fn,
    enabled: opts.enabled ?? true, refetchInterval: opts.refetchInterval,
  });
}

/** Mutation that toasts errors, optionally a success message, and invalidates this tenant's queries by prefix. */
export function useAct<A, R = unknown>(fn: (arg: A) => Promise<R>, o: { ok?: string; invalidate?: string[] ; onDone?: (r: R) => void } = {}) {
  const qc = useQueryClient();
  const toast = useToast();
  const { tenantId } = useAuth();
  return useMutation({
    mutationFn: fn,
    onSuccess: r => {
      if (o.ok) toast(o.ok, "ok");
      for (const k of o.invalidate ?? []) qc.invalidateQueries({ queryKey: [tenantId, k] });
      o.onDone?.(r);
    },
    onError: e => toast((e as Error).message, "err"),
  });
}
