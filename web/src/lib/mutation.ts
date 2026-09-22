/* Shared mutation helper (plan task 2): the single path for every mutating
 * call in the operate and admin views. Wraps api.ts (CSRF header and error
 * envelope already handled there): toasts success (auto-dismiss, never a
 * secret) and failure (error code plus message, persists until dismissed),
 * then refetches the affected query so the audit-visible state lands.
 * No ad hoc fetch in components. */
"use client";

import { useToast } from "@/components/ui/Toast";
import { api, ApiError } from "@/lib/api";

export type MutationMethod = "post" | "patch" | "del";

export interface MutateSpec<T> {
  /** Defaults to "post". */
  method?: MutationMethod;
  body?: unknown;
  /** Success toast title, e.g. "Key created". Must never contain secrets. */
  okTitle: string;
  okBody?: string | ((result: T) => string | undefined);
  /** Refetch of the affected query, run after a successful mutation. */
  refetch?: () => void | Promise<void>;
}

export type MutateFn = <T = unknown>(path: string, spec: MutateSpec<T>) => Promise<T | null>;

/** Hook: one toast-issuing mutate function per view. */
export function useMutate(): MutateFn {
  const { push } = useToast();

  return async function mutate<T = unknown>(path: string, spec: MutateSpec<T>): Promise<T | null> {
    try {
      const method = spec.method ?? "post";
      const result =
        method === "del"
          ? await api.del<T>(path)
          : method === "patch"
            ? await api.patch<T>(path, spec.body)
            : await api.post<T>(path, spec.body);
      push({
        tone: "ok",
        title: spec.okTitle,
        body: typeof spec.okBody === "function" ? spec.okBody(result as T) : spec.okBody,
      });
      if (spec.refetch) await spec.refetch();
      return result;
    } catch (err) {
      if (err instanceof ApiError) {
        push({ tone: "err", title: err.code, body: err.message });
      } else {
        push({
          tone: "err",
          title: "request_failed",
          body: err instanceof Error ? err.message : "The request could not be completed.",
        });
      }
      return null;
    }
  };
}
