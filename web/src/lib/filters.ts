/* URL-synced filter state with 250 ms debounce on text inputs.
 * Contract: docs/design-language.md section 4.13. Filters are mirrored into the
 * URL query string so a view is shareable and reloadable. `Clear all` appears
 * only when at least one filter is set.
 */
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

const DEBOUNCE_MS = 250;

export type TimerId = number | undefined;

export function useSyncedFilters<T extends Record<string, string>>(
  defaults: T,
): { filters: T; setFilter: (key: keyof T, value: string) => void; clearAll: () => void; active: boolean } {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const timer = useRef<TimerId>(undefined);

  const filters = useMemo(() => {
    const next = { ...defaults };
    for (const key of Object.keys(defaults)) {
      const value = searchParams.get(key);
      if (value !== null) next[key as keyof T] = value as T[keyof T];
    }
    return next;
  }, [searchParams, defaults]);

  const active = useMemo(
    () => Object.keys(defaults).some((key) => filters[key] !== defaults[key]),
    [filters, defaults],
  );

  const push = useCallback(
    (next: T) => {
      const params = new URLSearchParams();
      for (const key of Object.keys(next)) {
        if (next[key] !== defaults[key] && next[key] !== "") params.set(key, next[key]);
      }
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [router, pathname, defaults],
  );

  const setFilter = useCallback(
    (key: keyof T, value: string) => {
      const merged = { ...filters, [key]: value } as T;
      clearTimeout(timer.current);
      timer.current = window.setTimeout(() => push(merged), DEBOUNCE_MS);
    },
    [filters, push],
  );

  const clearAll = useCallback(() => {
    clearTimeout(timer.current);
    push({ ...defaults });
  }, [push, defaults]);

  useEffect(
    () => () => {
      clearTimeout(timer.current);
    },
    [],
  );

  return { filters, setFilter, clearAll, active };
}

/* Local text state with 250 ms debounce for inputs whose committed value
 * lives in the URL (avoids re-rendering the input on every keystroke). */
export function useDebouncedValue(value: string, onCommit: (value: string) => void): [string, (v: string) => void] {
  const [local, setLocal] = useState(value);
  const timer = useRef<TimerId>(undefined);
  const commit = useRef(onCommit);
  commit.current = onCommit;

  useEffect(() => setLocal(value), [value]);
  useEffect(
    () => () => {
      clearTimeout(timer.current);
    },
    [],
  );

  const update = useCallback((next: string) => {
    setLocal(next);
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => commit.current(next), DEBOUNCE_MS);
  }, []);

  return [local, update];
}
