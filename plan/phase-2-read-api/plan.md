# Phase 2 - Read API and streaming

Status: planned
Depends on: phase 1
Estimated effort: 3 to 4 days
Deliverable: the full read surface of `/api/v1` plus the SSE stream, with legacy shims so existing
clients and the old admin page keep working.

## Objective

Expose what Phase 1 records: traces, observations, metrics, logs, meta, models and settings read
paths, plus a live event stream and static serving of the built UI. No writes that require users yet.

## Scope

In: `/api/v1` read resources, cursor pagination, filter validation, error envelope, gzip, SSE stream
with replay, static UI serving wired to the real export, legacy `/admin/api/*` shims, API contract
tests, query performance checks.

Out: auth and user management (Phase 3), mutations other than trace delete, tags and scores, any UI
implementation beyond serving `web/out`.

## Deliverables

```
layawatch/api/health.py        /healthz, /, /api/v1/meta
layawatch/api/traces.py        list, detail, observations, tags, scores, delete
layawatch/api/metrics.py       metrics, summary, models mix
layawatch/api/logs.py          tail and export
layawatch/api/models.py        list, load, unload
layawatch/api/settings.py      read (write lands in Phase 3 with roles)
layawatch/api/stream.py        SSE endpoint, client registry, replay window
layawatch/api/legacy.py        /admin and /admin/api/* shims plus deprecation counters
layawatch/store/queries.py     read queries with allowlisted filters and cursor helpers
tests/                         contract tests per resource, SSE framing, shim mapping, perf checks
```

## Tasks

1. Implement the trace list query with allowlisted filters (`route`, `status` or class, `model`, `q`,
   `min_duration_ms`, `tag`, `since`/`until`/`range`) and keyset pagination on `(ts_start, id)`;
   reject unknown parameters with `400 invalid_filter`.
2. Implement trace detail: trace, observations ordered by `start_ms`, scores, related log lines,
   plus a `summary` block (forward share, queue share, framework overhead) computed server-side so
   the UI never does math the docs promise.
3. Implement `/api/v1/metrics` with step selection (10 s, 1 m, 1 h) from `metric_rollup`,
   `/api/v1/metrics/summary` with previous-window deltas, and `/api/v1/metrics/models` for the mix
   chart. Include the `note` field stating bucket-level percentiles.
4. Implement `/api/v1/logs` with level, substring, trace id filters and cursor pagination, plus a
   JSONL export for admin and owner roles.
5. Implement `/api/v1/models` (list, load, unload) delegating to the engine adapter with the
   single-in-flight guard and audit entries.
6. Implement `api/stream.py`: client registry, `hello`/`pulse`/`trace`/`log`/`model`/`ping` events,
   `LAYWATCH_STREAM_TICK` cadence, replay of up to 200 missed events on `Last-Event-ID`, per-session
   connection cap, and clean removal on client disconnect.
7. Implement gzip for JSON responses over 1 KB, `X-Request-Id` echo on all responses, and the shared
   error envelope for every handler (including unexpected exceptions: 500 with a code, stack trace to
   logs only).
8. Implement legacy shims: `GET /admin` to `/`, `/admin/api/*` mapped onto the new handlers, with
   `Deprecation` and `Link` headers and a per-endpoint counter exposed in `/api/v1/meta`.
9. Wire `http/static.py` to the real `web/out` once Phase 4 produces it; until then verify with a
   fixture tree including nested dynamic routes and a 404 page.
10. Contract tests for every resource: status codes, error codes, filter rejection, pagination
    stability under concurrent inserts, SSE framing (event names, blank-line separation, replay),
    shim mapping and deprecation headers.
11. Performance check: with 10,000 traces seeded, assert list queries and the metrics endpoint return
    in under 50 ms p95 on the reference machine, and record the numbers.

## Acceptance criteria

1. `GET /api/v1/traces?status=500&range=1h&limit=10` returns only 500s, newest first, with a working
   `next_cursor`; `?bogus=1` returns `400 invalid_filter`.
2. Trace detail for a real traced request returns observations whose `start_ms` order matches the
   waterfall order documented in `docs/design-language.md` section 4.15.
3. `GET /api/v1/metrics?metrics=requests,latency,queue&range=15m` returns 10 s points covering the
   window with no gaps, and a 1 h range returns 1 m points.
4. SSE: `curl -N /api/v1/stream` emits `hello`, then `pulse` every tick, a `trace` event per completed
   request, and `ping` every 15 s; killing and reconnecting with `Last-Event-ID` replays missed traces.
5. `/api/v1/meta` reports `obs_dropped_total`, `write_queue_depth`, `db_size_bytes`, `rss_mb`,
   `sse_clients` with plausible values.
6. Legacy: `GET /admin` redirects to `/`; `/admin/api/stats` returns the new summary shape with
   `Deprecation: true`; the deprecation counter in `/api/v1/meta` increments.
7. Static: `/` serves the UI when `web/out` exists, assets carry immutable cache headers, HTML is
   `no-cache`, `/traces/abc` resolves to the exported route, unknown paths return the 404 page.
8. Seeded 10k-trace performance numbers are recorded and within the stated budget.

## Evidence required

- `curl` transcripts for each acceptance criterion, including the error envelope and a rejected filter.
- `curl -N` capture of 30 s of SSE showing all event types and one reconnect with replay.
- Seeded-database timing table (endpoint, p50, p95, row count).
- Headers dump showing cache policy, gzip and deprecation headers.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| N+1 queries on trace detail | one query per table, batch by `trace_id IN (...)`, asserted by a query-count test |
| SSE blocking the threading server under many clients | short writes with socket timeout, per-session cap, heartbeat that drops dead clients |
| Shim mapping drift from the legacy shapes | contract tests compare shim output against recorded legacy responses captured before the change |
| Pagination instability while inserting | keyset pagination on `(ts_start, id)` only; no offset queries |

## Rollback

The API is additive: legacy endpoints keep working, and `/api/v1` can be disabled with
`LAYWATCH_API=0` (serves only engine routes plus `/healthz`) if a defect blocks operators.

## Exit gate

All eight criteria verified, evidence recorded, decisions recorded for pagination, step selection and
shim lifetime.
