/* Logs view: server-side filters (level, contains, trace id) hitting
 * GET /api/v1/logs with the shared 250 ms debounce, cursor paging with
 * `Load older`, and SSE `log` events appended to the tail. Pause, Clear and
 * Jump to latest live in LogViewer. Plan: phase-5 task 10; criterion 7. */
"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import SectionHeader from "@/components/ui/SectionHeader";
import FilterBar from "@/components/ui/FilterBar";
import Pagination from "@/components/ui/Pagination";
import LogViewer from "@/components/ui/LogViewer";
import Card from "@/components/ui/Card";
import EmptyState, { ErrorState, Skeleton } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Input } from "@/components/ui/Input";
import { useStream } from "@/lib/stream";
import { useDebouncedValue, useSyncedFilters } from "@/lib/filters";
import { api, ApiError } from "@/lib/api";
import type { LogEntry, Paged } from "@/lib/api";
import { thousands } from "@/lib/format";

type LoadState = "loading" | "ready" | "empty" | "error";

const DEFAULTS = { level: "", q: "", trace_id: "" };
const PAGE_LIMIT = 200;

export default function LogsPage() {
  return (
    <Suspense fallback={null}>
      <LogsContent />
    </Suspense>
  );
}

function LogsContent() {
  const { filters, setFilter, clearAll, active } = useSyncedFilters(DEFAULTS);
  const [state, setState] = useState<LoadState>("loading");
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [fetchKey, setFetchKey] = useState(0);
  const [detail, setDetail] = useState<string | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);
  const [localQ, setLocalQ] = useDebouncedValue(filters.q, (value) => setFilter("q", value));
  const [localTrace, setLocalTrace] = useDebouncedValue(
    filters.trace_id,
    (value) => setFilter("trace_id", value),
  );

  const buildQuery = useCallback(() => {
    const query = new URLSearchParams({ limit: String(PAGE_LIMIT) });
    for (const key of Object.keys(filters) as Array<keyof typeof DEFAULTS>) {
      if (filters[key]) query.set(key, filters[key]);
    }
    return query;
  }, [filters]);

  const load = useCallback(() => {
    setState("loading");
    api
      .get<Paged<LogEntry>>(`/api/v1/logs?${buildQuery().toString()}`)
      .then((page) => {
        setEntries(page.items);
        setCursor(page.next_cursor);
        setTotal(page.total_estimate);
        setFetchKey((key) => key + 1);
        setState(page.items.length === 0 ? "empty" : "ready");
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setDetail(`${err.status} ${err.code}: ${err.message}`);
          setRequestId(err.requestId);
        } else {
          setDetail("The server could not be reached. Check that the process is running.");
          setRequestId(undefined);
        }
        setState("error");
      });
  }, [buildQuery]);

  useEffect(load, [load]);

  const loadOlder = () => {
    if (!cursor) return;
    setLoadingMore(true);
    api
      .get<Paged<LogEntry>>(
        `/api/v1/logs?${buildQuery().toString()}&cursor=${encodeURIComponent(cursor)}`,
      )
      .then((page) => {
        /* Newest first: older pages append after the current tail. */
        setEntries((prev) => [...prev, ...page.items]);
        setCursor(page.next_cursor);
        setTotal(page.total_estimate);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) setDetail(`${err.status} ${err.code}: ${err.message}`);
        else setDetail("The server could not be reached. Check that the process is running.");
        setState("error");
      })
      .finally(() => setLoadingMore(false));
  };

  useStream({
    onLog: (entry) => {
      setEntries((prev) => [entry, ...prev]);
      setTotal((count) => count + 1);
      setState((prev) => (prev === "empty" ? "ready" : prev));
    },
    onPoll: (data) => {
      if (!data.logs) return;
      setEntries(data.logs.items);
      setCursor(data.logs.next_cursor);
      setTotal(data.logs.total_estimate);
      setFetchKey((key) => key + 1);
    },
  });

  const meta = filters.trace_id
    ? `trace ${filters.trace_id} · newest first`
    : "newest first";

  return (
    <section className="lw-sec" aria-label="Log tail">
      <SectionHeader eyebrow="Observe" title="Log tail" meta={meta} />
      <FilterBar active={active} onClearAll={clearAll}>
        <Select
          label="level"
          size="sm"
          value={filters.level}
          onChange={(event) => setFilter("level", event.target.value)}
        >
          <option value="">all</option>
          <option value="debug">debug</option>
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="error">error</option>
        </Select>
        <Input
          label="contains"
          size="sm"
          placeholder="filter text, e.g. 9f2c1a4b"
          value={localQ}
          onChange={(event) => setLocalQ(event.target.value)}
        />
        <Input
          label="trace id"
          size="sm"
          placeholder="filter by request id"
          value={localTrace}
          onChange={(event) => setLocalTrace(event.target.value)}
        />
        {filters.trace_id ? (
          <Link
            className="lw-btn lw-btn-ghost lw-btn-sm"
            href="/logs"
            style={{ alignSelf: "flex-end" }}
          >
            Clear trace filter
          </Link>
        ) : null}
      </FilterBar>
      <Card variant="flat">
        {state === "loading" ? (
          <div aria-busy="true" aria-label="Loading log lines">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <Skeleton height={12} />
              </div>
            ))}
          </div>
        ) : state === "error" ? (
          <ErrorState
            title="Failed to load logs"
            detail={detail}
            requestId={requestId}
            onRetry={load}
          />
        ) : state === "empty" ? (
          <EmptyState
            title="No log lines yet"
            body="Requests to /predict write log lines you can inspect here."
          />
        ) : (
          <LogViewer entries={entries} fetchKey={fetchKey} />
        )}
      </Card>
      <Pagination
        rangeLabel={`showing ${thousands(entries.length)} of ${thousands(total)} lines`}
        hasMore={cursor !== null && state === "ready"}
        onLoadOlder={loadOlder}
        loading={loadingMore}
      />
    </section>
  );
}
