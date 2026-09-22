/* Traces list: URL-reflected filters (route, status, model, request id, range)
 * over the shared TraceTable, cursor paging with `Load older`, and SSE `trace`
 * events prepended client-side (filtered, deduped, announced as a count).
 * Plan: phase-5 tasks 2 and 5; criteria 2, 3 and 4. */
"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import FilterBar from "@/components/ui/FilterBar";
import Pagination from "@/components/ui/Pagination";
import Card from "@/components/ui/Card";
import TraceTable from "@/components/ui/TraceTable";
import { Select } from "@/components/ui/Select";
import { Input } from "@/components/ui/Input";
import type { TableState } from "@/components/ui/Table";
import { useStream } from "@/lib/stream";
import { useDebouncedValue, useSyncedFilters } from "@/lib/filters";
import { api, ApiError } from "@/lib/api";
import type { MetaResponse, Paged, TraceSummary } from "@/lib/api";
import { thousands } from "@/lib/format";

const DEFAULTS = { route: "", status: "", model: "", q: "", range: "" };
type Filters = typeof DEFAULTS;
const PAGE_LIMIT = 50;

function matchesStatus(status: number, filter: string): boolean {
  if (!filter) return true;
  if (filter.endsWith("xx")) {
    const prefix = Number.parseInt(filter[0], 10);
    return Math.floor(status / 100) === prefix;
  }
  return status === Number.parseInt(filter, 10);
}

function matchesFilters(trace: TraceSummary, filters: Filters): boolean {
  if (filters.route && trace.route !== filters.route) return false;
  if (filters.model && trace.model !== filters.model) return false;
  if (!matchesStatus(trace.status, filters.status)) return false;
  if (filters.q) {
    const needle = filters.q.toLowerCase();
    const inId = trace.id.toLowerCase().includes(needle);
    const inError = (trace.error_code ?? "").toLowerCase().includes(needle);
    if (!inId && !inError) return false;
  }
  return true;
}

export default function TracesPage() {
  return (
    <Suspense fallback={null}>
      <TracesContent />
    </Suspense>
  );
}

function TracesContent() {
  const { filters, setFilter, clearAll, active } = useSyncedFilters(DEFAULTS);
  const [state, setState] = useState<TableState>("loading");
  const [items, setItems] = useState<TraceSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [detail, setDetail] = useState<string | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);
  const [newTraces, setNewTraces] = useState(0);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [localQ, setLocalQ] = useDebouncedValue(filters.q, (value) => setFilter("q", value));
  const pagedRef = useRef(false);

  const buildQuery = useCallback(() => {
    const query = new URLSearchParams({ limit: String(PAGE_LIMIT) });
    for (const key of Object.keys(filters) as Array<keyof Filters>) {
      if (filters[key]) query.set(key, filters[key]);
    }
    return query;
  }, [filters]);

  const load = useCallback(() => {
    pagedRef.current = false;
    setState("loading");
    api
      .get<Paged<TraceSummary>>(`/api/v1/traces?${buildQuery().toString()}`)
      .then((page) => {
        setItems(page.items);
        setCursor(page.next_cursor);
        setTotal(page.total_estimate);
        setState(page.items.length === 0 ? (active ? "filtered-empty" : "empty") : "ready");
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
  }, [buildQuery, active]);

  useEffect(load, [load]);

  /* Keep the list filters reachable from the detail page's back link. */
  useEffect(() => {
    try {
      sessionStorage.setItem("lw:traces-query", window.location.search);
    } catch {
      /* storage unavailable: the back link falls back to the bare list */
    }
  }, [filters]);

  useEffect(() => {
    api
      .get<MetaResponse>("/api/v1/meta")
      .then((meta) => setModelOptions(meta.config?.models ?? []))
      .catch(() => {});
  }, []);

  const loadOlder = () => {
    if (!cursor) return;
    setLoadingMore(true);
    api
      .get<Paged<TraceSummary>>(
        `/api/v1/traces?${buildQuery().toString()}&cursor=${encodeURIComponent(cursor)}`,
      )
      .then((page) => {
        pagedRef.current = true;
        setItems((prev) => [...prev, ...page.items]);
        setCursor(page.next_cursor);
        setTotal(page.total_estimate);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setDetail(`${err.status} ${err.code}: ${err.message}`);
          setRequestId(err.requestId);
        } else {
          setDetail("The server could not be reached. Check that the process is running.");
        }
        setState("error");
      })
      .finally(() => setLoadingMore(false));
  };

  useStream({
    onTrace: (trace) => {
      if (!matchesFilters(trace, filters)) return;
      setItems((prev) => {
        if (prev.some((item) => item.id === trace.id)) return prev;
        return [trace, ...prev].slice(0, 200);
      });
      setTotal((count) => count + 1);
      setState((prev) => (prev === "empty" || prev === "filtered-empty" ? "ready" : prev));
      setNewTraces((count) => count + 1);
    },
    onPoll: (data) => {
      if (!data.traces || pagedRef.current) return;
      setItems(data.traces.items);
      setCursor(data.traces.next_cursor);
      setTotal(data.traces.total_estimate);
    },
  });

  return (
    <section className="lw-sec" aria-label="Live traces">
      <div className="lw-sr-only" role="status" aria-live="polite">
        {newTraces > 0 ? `${newTraces} new trace${newTraces === 1 ? "" : "s"}` : ""}
      </div>
      <SectionHeader
        eyebrow="Observe"
        title="Live traces"
        meta="ring newest first · click a row for the waterfall"
      />
      <FilterBar active={active} onClearAll={clearAll}>
        <Select
          label="route"
          size="sm"
          value={filters.route}
          onChange={(event) => setFilter("route", event.target.value)}
        >
          <option value="">all</option>
          <option value="/predict">/predict</option>
          <option value="/route">/route</option>
        </Select>
        <Select
          label="status"
          size="sm"
          value={filters.status}
          onChange={(event) => setFilter("status", event.target.value)}
        >
          <option value="">all</option>
          <option value="200">200</option>
          <option value="422">422</option>
          <option value="429">429</option>
          <option value="500">500</option>
          <option value="4xx">4xx</option>
          <option value="5xx">5xx</option>
        </Select>
        <Select
          label="model"
          size="sm"
          value={filters.model}
          onChange={(event) => setFilter("model", event.target.value)}
        >
          <option value="">all</option>
          {modelOptions.map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </Select>
        <Input
          label="request id"
          size="sm"
          placeholder="filter request id"
          value={localQ}
          onChange={(event) => setLocalQ(event.target.value)}
        />
        <Select
          label="range"
          size="sm"
          value={filters.range}
          onChange={(event) => setFilter("range", event.target.value)}
        >
          <option value="">all</option>
          <option value="15m">15m</option>
          <option value="1h">1h</option>
          <option value="6h">6h</option>
          <option value="24h">24h</option>
          <option value="7d">7d</option>
        </Select>
      </FilterBar>
      <Card variant="flat">
        <TraceTable
          items={items}
          state={state}
          onClearFilters={clearAll}
          errorDetail={detail}
          requestId={requestId}
          onRetry={load}
        />
      </Card>
      <Pagination
        rangeLabel={`showing ${thousands(items.length)} of ${thousands(total)}`}
        hasMore={cursor !== null && state === "ready"}
        onLoadOlder={loadOlder}
        loading={loadingMore}
      />
    </section>
  );
}
