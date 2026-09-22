/* Audit view (plan phase-6 task 11): URL-reflected actor, action and range
 * filters over GET /api/v1/audit, the AuditRow layout (design 4.31) and
 * cursor pagination with Load older. Read-only, admin+ only, linked from the
 * Settings footer with no sidebar entry. The endpoint may not exist yet, so
 * every failure renders the defensive error state with the server envelope. */
"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import SectionHeader from "@/components/ui/SectionHeader";
import FilterBar from "@/components/ui/FilterBar";
import Pagination from "@/components/ui/Pagination";
import Card from "@/components/ui/Card";
import AuditRow from "@/components/ui/AuditRow";
import { Select } from "@/components/ui/Select";
import { Input } from "@/components/ui/Input";
import Segmented, { type SegmentOption } from "@/components/ui/Segmented";
import EmptyState, { ErrorState, Skeleton } from "@/components/ui/EmptyState";
import { useAuth } from "@/components/AuthProvider";
import { can } from "@/lib/permissions";
import { useDebouncedValue, useSyncedFilters } from "@/lib/filters";
import { api, ApiError } from "@/lib/api";
import type { AuditEntry, AuditEnvelope } from "@/lib/api";
import { clockTime, isoWithTz } from "@/lib/format";

const DEFAULTS = { actor: "", action: "", range: "24h" };
type Filters = typeof DEFAULTS;
type LoadState = "loading" | "ready" | "empty" | "filtered-empty" | "error";

/* Action list from docs/api-reference.md section 13. */
const ACTIONS = [
  "auth.login",
  "auth.login_failed",
  "auth.logout",
  "key.created",
  "key.rotated",
  "key.revoked",
  "key.auth_changed",
  "model.loaded",
  "model.unloaded",
  "user.created",
  "user.role_changed",
  "user.disabled",
  "user.deleted",
  "session.revoked_all",
  "settings.updated",
  "trace.deleted",
  "payload_capture.toggled",
];

const RANGES: SegmentOption[] = [
  { value: "1h", label: "1h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "", label: "all" },
];

function resultTone(result: string | undefined): "ok" | "err" | "neutral" {
  if (result === "ok") return "ok";
  if (result === "denied") return "err";
  return "neutral";
}

export default function AuditPage() {
  return (
    <Suspense fallback={null}>
      <AuditContent />
    </Suspense>
  );
}

function AuditContent() {
  const { me } = useAuth();
  const { filters, setFilter, clearAll, active } = useSyncedFilters(DEFAULTS);
  const [localActor, setLocalActor] = useDebouncedValue(filters.actor, (value) =>
    setFilter("actor", value),
  );
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [state, setState] = useState<LoadState>("loading");
  const [detail, setDetail] = useState<string | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);
  const pagedRef = useRef(false);

  const canRead = can(me, "audit.read");

  const buildQuery = useCallback(() => {
    const query = new URLSearchParams();
    if (filters.actor) query.set("actor", filters.actor);
    if (filters.action) query.set("action", filters.action);
    if (filters.range) query.set("range", filters.range);
    return query;
  }, [filters]);

  const load = useCallback(() => {
    pagedRef.current = false;
    setState("loading");
    api
      .get<AuditEnvelope>(`/api/v1/audit?${buildQuery().toString()}`)
      .then((page) => {
        const rows = page.items ?? [];
        setItems(rows);
        setCursor(page.next_cursor ?? null);
        setTotal(page.total_estimate ?? rows.length);
        setState(
          rows.length === 0 ? (active ? "filtered-empty" : "empty") : "ready",
        );
        setDetail(undefined);
        setRequestId(undefined);
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

  useEffect(() => {
    if (!canRead) return;
    load();
  }, [canRead, load]);

  const loadOlder = () => {
    if (!cursor) return;
    setLoadingMore(true);
    const query = buildQuery();
    query.set("cursor", cursor);
    api
      .get<AuditEnvelope>(`/api/v1/audit?${query.toString()}`)
      .then((page) => {
        pagedRef.current = true;
        const rows = page.items ?? [];
        setItems((prev) => [...prev, ...rows]);
        setCursor(page.next_cursor ?? null);
        setTotal(page.total_estimate ?? 0);
      })
      .catch((err: unknown) => {
        setDetail(
          err instanceof ApiError
            ? `${err.status} ${err.code}: ${err.message}`
            : "The server could not be reached.",
        );
        setState("error");
      })
      .finally(() => setLoadingMore(false));
  };

  if (!me) {
    return (
      <section className="lw-sec" aria-label="Audit log">
        <SectionHeader eyebrow="Admin" title="Audit log" meta="checking session" />
        <Card variant="flat" aria-busy="true">
          <span className="lw-sr-only" role="status">
            Checking session
          </span>
          <Skeleton height={14} width="60%" />
        </Card>
      </section>
    );
  }

  if (!canRead) {
    return (
      <section className="lw-sec" aria-label="Audit log">
        <SectionHeader eyebrow="Admin" title="Audit log" meta="access denied" />
        <Card variant="flat">
          <EmptyState
            title="Access denied"
            body={
              "The audit log needs the admin role. Viewer accounts are read-only;" +
              " ask an owner for access."
            }
          />
        </Card>
      </section>
    );
  }

  return (
    <section className="lw-sec" aria-label="Audit log">
      <SectionHeader
        eyebrow="Admin"
        title="Audit log"
        meta="append-only - every mutation writes one row"
      />
      <FilterBar active={active} onClearAll={clearAll}>
        <Input
          label="actor"
          size="sm"
          placeholder="filter actor"
          value={localActor}
          onChange={(event) => setLocalActor(event.target.value)}
        />
        <Select
          label="action"
          size="sm"
          value={filters.action}
          onChange={(event) => setFilter("action", event.target.value)}
        >
          <option value="">all</option>
          {ACTIONS.map((action) => (
            <option key={action} value={action}>
              {action}
            </option>
          ))}
        </Select>
        <Segmented
          label="range"
          options={RANGES}
          value={filters.range}
          onChange={(value) => setFilter("range", value)}
        />
      </FilterBar>
      <Card variant="flat">
        {state === "loading" ? (
          <div aria-busy="true" aria-label="Loading audit entries">
            {Array.from({ length: 6 }, (_, index) => (
              <div key={index} style={{ padding: "10px 0" }}>
                <Skeleton height={13} width="45%" />
              </div>
            ))}
          </div>
        ) : state === "error" ? (
          <ErrorState
            title="Failed to load audit entries"
            detail={detail}
            requestId={requestId}
            onRetry={load}
          />
        ) : state === "empty" ? (
          <EmptyState
            title="No audit entries yet"
            body="Mutations such as key creation and role changes appear here."
          />
        ) : state === "filtered-empty" ? (
          <EmptyState
            title="No rows match these filters"
            body="Adjust the actor, action or range, or clear the filters."
          />
        ) : (
          <div>
            {items.map((entry, index) => (
              <AuditRow
                key={`${entry.ts ?? "x"}-${entry.action ?? ""}-${index}`}
                ts={typeof entry.ts === "number" ? clockTime(entry.ts) : "-"}
                actor={entry.actor ?? "unknown"}
                action={entry.action ?? "unknown"}
                target={entry.target ?? undefined}
                result={entry.result ?? "unknown"}
                tone={resultTone(entry.result)}
              />
            ))}
          </div>
        )}
      </Card>
      <Pagination
        rangeLabel={`showing ${items.length} of ${total}`}
        hasMore={cursor !== null && state === "ready"}
        onLoadOlder={loadOlder}
        loading={loadingMore}
      />
    </section>
  );
}
