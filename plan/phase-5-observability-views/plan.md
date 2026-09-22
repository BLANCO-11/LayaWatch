# Phase 5 - Observability views

Status: planned
Depends on: phase 4
Estimated effort: 5 to 7 days
Deliverable: the four Observe views (Overview, Traces, Metrics, Logs) plus the trace detail page
render live data from `/api/v1` and the SSE stream, with the full state matrix and measured
interaction budgets on a 10k-trace database.

## Objective

Turn the Phase 2 read API and SSE stream and the Phase 4 component catalog into the working
observability console: an operator opens LayaWatch and answers "is it healthy, what just happened,
why was it slow, and what is it saying" without leaving the page.

## Scope

In: Overview KPI strip with deltas from `GET /api/v1/metrics/summary` (including the conditional
`Throttled (5m)` cell from `docs/rate-limiting.md` section 5), request rate and latency charts,
recent traces table, system pulse strip; Traces list with URL-reflected filter bar, cursor
pagination, sticky header and the full state matrix; Trace detail with chips, waterfall per
`docs/design-language.md` section 4.15, span metadata expansion, scores timeline, related log lines,
summary line and copyable request id; Metrics view with four charts plus model mix and server-driven
step; Logs view with server-side filters, follow mode and virtualization; the shared SSE client with
`Last-Event-ID` reconnect and polling fallback; `aria-live` announcements; keyboard and screen-reader
passes; interaction-budget verification against a seeded 10k-trace database.

Out: Playground, Checkpoints, API Keys, Users, Audit, Settings views (Phase 6); auth, roles and
sessions themselves (Phase 3); any API or storage change (Phase 2 deliverables are consumed as-is);
performance optimization beyond the budgets already stated in `docs/performance.md` (Phase 7);
packaging (Phase 8).

## Deliverables

```
web/src/app/overview/page.tsx            Overview route (client component, static export safe)
web/src/app/traces/page.tsx              Traces list route
web/src/app/traces/[id]/page.tsx         Trace detail route
web/src/app/metrics/page.tsx             Metrics route
web/src/app/logs/page.tsx                Logs route
web/src/lib/live.ts                      SSE client: hello/pulse/trace/log/model/ping, Last-Event-ID, backoff, polling fallback
web/src/lib/filters.ts                   allowlisted filter state <-> URL query string, 250 ms debounce
web/src/views/overview/KpiStrip.tsx      KPI cells with deltas, conditional Throttled (5m) cell
web/src/views/overview/PulseStrip.tsx    self-observability strip from /api/v1/meta (obs model section 9)
web/src/views/overview/RecentTraces.tsx  recent trace rows fed by ring + SSE
web/src/views/traces/FilterBar.tsx       route, status, model, request id, range controls
web/src/views/traces/TraceTable.tsx      sticky header table, Load older cursor paging, state matrix
web/src/views/traces/TraceDetail.tsx     header chips, back link, CopyField request id, summary line
web/src/views/traces/Waterfall.tsx       ordered span rows per design language 4.15 with inline expansion
web/src/views/traces/ScoresTimeline.tsx  scores and lifecycle events side rail per design language 4.17
web/src/views/metrics/RateChart.tsx      requests per second area chart
web/src/views/metrics/LatencyChart.tsx   p50/p90/p95 percentile lines
web/src/views/metrics/ErrorChart.tsx     error rate, --err series
web/src/views/metrics/QueueChart.tsx     queue wait, --warn series
web/src/views/metrics/ModelMix.tsx       stacked bars per bucket from /api/v1/metrics/models
web/src/views/logs/LogViewer.tsx         tail well, follow/pause/clear, virtualized beyond 500 lines
web/src/components/charts/               LineChart.tsx, AreaChart.tsx, StackedBar.tsx (extend Phase 4 set)
tests/ui/test_observe_views.py           Playwright: live insert, filters, states, a11y, budgets
tests/artifacts/phase5/                  timing JSON and screenshots produced by the suite
```

## Tasks

1. Route and data layer: create the five routes above as client components under `web/src/app/`,
   reusing the Phase 4 shell, page header pattern (mono eyebrow plus serif title) and the shared
   `Skeleton`/`EmptyState`/`ErrorState` components; fetch through the Phase 4 API helper against
   `docs/api-reference.md` sections 5 to 8. Any component prop missing from
   `docs/design-language.md` is added to that document first, per its implementation contract.
2. `web/src/lib/filters.ts`: model the allowlisted trace filters (`route`, `status`, `model`, `q`,
   `range`) as state mirrored in the URL query string (design language 4.13) so a view is
   shareable and reloadable; write only known keys, debounce text inputs 250 ms, expose a
   `Clear all` condition; never send a parameter the API would reject with `invalid_filter`.
3. Overview KPI strip: read `GET /api/v1/metrics/summary`, render label, value with inline unit and
   delta line per design language 4.10 (`+0.4 vs prev 5m`, direction colored `ok`/`warn`/`err`);
   render the `Throttled (5m)` cell only when the count is non-zero (`docs/rate-limiting.md`
   section 5) so a healthy deployment carries no permanent zero; add the System pulse strip from
   `/api/v1/meta` counters (`obs_dropped_total`, `write_queue_depth`, `write_latency_ms`,
   `db_size_bytes`, `rss_mb`, `ring_usage`, `sse_clients`).
4. Overview charts and recent traces: request rate (area) and latency p50/p95 (line) over the
   rolling 5-minute window from `/api/v1/metrics`, plus the recent traces table fed from
   `/api/v1/traces?limit=10` and prepended by SSE `trace` events; each row links to the detail
   route; section headers carry the window meta (`rolling 5-minute windows`).
5. Traces list: `FilterBar` (route select, status input or class, model select, request id search,
   range select `15m/1h/6h/24h/7d`) above `TraceTable` with sticky header below the 56 px topbar,
   tabular right-aligned numeric columns, status Badges (`200 OK`, `422 reject`, `500 error`),
   ids in accent mono; `Load older` cursor button plus range label (`showing 50 of 1,204`) per
   design language 4.12; implement all four table states: 6-row skeleton, empty
   (`No traces yet. Send a request to /predict to see one.`), error with retry and copyable request
   id, and filtered-to-nothing with a clear-filters action.
6. Trace detail: header with ghost back link before the eyebrow, copyable request id via `CopyField`,
   chips for `model`, `route_reason`, state size and question count (`body.parse` attributes) and
   client key; payload capture warning banner when `meta.payload_capture` is set (observability
   model section 4); server-provided summary block rendered as the required summary line: total
   duration, forward share, queue share, status, absolute start time.
7. Waterfall: rows ordered by `start_ms` from `/api/v1/traces/{id}/observations`; layout name 110 px,
   start 64 px, track, duration 64 px; colors strictly per design language 4.15: standard spans
   `--accent-bar`, `queue.wait` `--warn`, `forward` `--s2`, `error` `--err`; bar width proportional
   to trace total with 2 px minimum; hover tooltip with absolute start and duration; click expands
   the span attributes JSON inline (truncated, one at a time); `<ol>`/`<li>` semantics with
   per-row `aria-label` of name, start and duration; every duration printed as text so bar length
   is never the only signal.
8. Scores timeline: render `scores[]` and lifecycle events (model loaded, retention applied) in the
   side rail per design language 4.17, plus related `logs[]` as a severity-colored log excerpt
   linking to the Logs view pre-filtered by `trace_id`.
9. Metrics view: range segmented control (`15m`, `6h`, `7d`) sending `range` to
   `/api/v1/metrics`; the server picks the step (10 s for <= 15 m, 1 m for <= 6 h, 1 h beyond,
   observability model section 6) and the client plots the returned points verbatim: never
   re-bucket, never interpolate, gaps render as breaks; four charts (rate, latency p50/p90/p95,
   error rate in `--err`, queue wait in `--warn`) plus `ModelMix` stacked bars from
   `/api/v1/metrics/models`; every chart footer states `step 10s · window 15m`; section meta
   carries the bucket-level percentile note and the stated thresholds (p95 > 1 s, error rate > 2 %)
   as text, not colored zones.
10. Logs view: `LogViewer` with server-side filters (`level`, `q`, `trace_id`) hitting
    `GET /api/v1/logs` with the shared 250 ms debounce; follow mode with `Pause` (shows buffered
    line count), `Clear` (empties the local view only), `Jump to latest` (returns to follow) per
    design language 4.16; virtualize beyond 500 rendered lines; severity coloring touches only the
    timestamp, request id and severity tokens, never a whole line.
11. Live wiring: `web/src/lib/live.ts` opens `GET /api/v1/stream` via `EventSource`, handles
    `hello` (set tick and ring size), `pulse` (KPI strip and sidebar footer), `trace` (prepend to
    tables, dedupe by trace id), `log` (tail), `model` (loaded list), ignores `ping`, tracks the
    last event id so an auto-reconnect replays missed events (`Last-Event-ID`, at most 200 per the
    API reference); on repeated SSE failure fall back to polling `/api/v1/metrics/summary`,
    `/api/v1/traces` and `/api/v1/logs` at the `LAYWATCH_STREAM_TICK` cadence and flip the sidebar
    footer and live pill from `live` to `polling 3s` (design language 4.32); reconnect recovers to
    live without a page reload.
12. Accessibility and verification: wrap the stream state and new-trace counts in an `aria-live="polite"`
    region announcing counts only (`12 new traces`, design language section 7); verify every table
    is keyboard navigable through row links, every filter has a visible bound label, every chart
    exposes a text summary (latest, min, max, window) plus a visually hidden `<table>` alternative,
    focus rings are visible in both themes, and every one of the five views shows its empty and
    error state; seed 10k traces and record the interaction timings.

## Acceptance criteria

1. Overview renders the KPI cells returned by `/api/v1/metrics/summary` with value, unit and a
   delta line stating its comparison window; with zero throttles in the last 5 minutes no
   `Throttled (5m)` cell exists in the DOM, and after a 429 is recorded the cell appears with a
   non-zero count without a reload.
2. With the Traces view open, one `POST /predict` against the server adds its row to the table
   within one stream tick (default 3 s) without any reload, and the polite live region announces
   exactly a count (`1 new trace`), not row content.
3. Changing any filter writes it to the URL query string; pasting that URL restores the identical
   filtered view; `Clear all` appears only when a filter is set; no client-generated query produces
   a `400 invalid_filter` from the API.
4. `Load older` appends the next cursor page (no overlap, no gap under concurrent inserts) and the
   range label reads `showing <n> of <total_estimate>`; table shows skeleton (6 rows), empty,
   error-with-retry, and filtered-to-nothing states, each reachable and verified by the UI suite.
5. Trace detail shows the chips (model, route_reason, state size, question count, client key), a
   waterfall whose row order matches `start_ms`, per-span colors matching design language 4.15,
   inline attribute expansion (one open at a time), a printed duration on every row, the summary
   line with total, forward share and queue share, related log lines, the scores timeline, a back
   link to the list preserving prior filters, and a request id that copies to the clipboard.
6. The Metrics view renders rate, latency percentile, error rate and queue charts plus the model
   mix stacked bars; the footer of each chart states the server-chosen step and window; the
   section meta carries the bucket-level percentile note; point counts equal the API response
   points (no client re-bucketing), and gaps render as breaks.
7. Logs filters are server-side (each keystroke after debounce issues `GET /api/v1/logs` with the
   filter in the query string); `Pause` freezes the tail and shows the buffered count, `Clear`
   empties only the local view (a subsequent server fetch redraws the lines), `Jump to latest`
   resumes follow; with a 5,000-line fetch the rendered DOM stays below 600 log line nodes.
8. Seeded-data interaction budget: against `scripts/seed.py --traces 10000 --observations 9`, the
   UI suite measures filter-apply-to-painted-rows on Traces, trace detail open, metrics view load
   and logs filter at p95 <= 1000 ms each over at least 20 interactions, while the underlying API
   stays inside the `docs/performance.md` budgets (trace list <= 50 ms p95, metrics 15 m window
   <= 30 ms p95); timings are written to `tests/artifacts/phase5/timings.json`.
9. SSE failure and recovery: blocking the stream endpoint flips the live pill to `polling 3s`,
   data keeps updating via polling at the stream tick cadence, and restoring the endpoint returns
   the pill to `live` within 15 s without a page reload; duplicate `trace` events never produce
   duplicate rows.
10. A11y pass: every chart and the waterfall expose a text summary and (charts) a hidden data
    table; all five views were exercised empty and error; tables are fully keyboard operable with
    visible focus; both themes verified for every view with no contrast defect; no emoji or
    em-dash appears in any shipped string.

## Evidence required

- `python scripts/seed.py --traces 10000 --observations 9` output, followed by
  `pytest tests/ui/test_observe_views.py -q` results including the budget table and
  `tests/artifacts/phase5/timings.json`.
- `curl -N http://127.0.0.1:8050/api/v1/stream | tee tests/artifacts/phase5/sse.txt` showing
  `hello`, `pulse`, `trace`, `log`, `model`, `ping`, plus one forced reconnect with `Last-Event-ID`
  replay.
- Screenshots of Overview, Traces, trace detail, Metrics and Logs in both themes under
  `tests/artifacts/phase5/shots/`, including one shot of each empty and error state.
- HAR or network log proving the logs and traces filters are server-side query strings and the
  Throttled cell condition.
- `scripts/bench.py api` output re-run against the seeded database for the two API budgets.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| SSE reconnect loops or duplicated rows under flaky networks | dedupe by trace id, capped 200-event replay, exponential backoff, and the polling fallback keeps the view truthful when the stream is down |
| Conditional `Throttled (5m)` cell causes a layout shift when it appears | cell occupies the last grid slot of the flat KPI strip; the strip is auto-fit so existing cells never resize mid-paint |
| Client-side chart code starts re-bucketing or smoothing points | single plot component takes API points verbatim; acceptance criterion 6 asserts point counts; gaps stay gaps |
| Log tail floods the DOM and freezes the tab | virtualization beyond 500 lines (criterion 7), server-side filtering keeps fetch sizes bounded |
| URL filter state drifts from the API allowlist and produces `invalid_filter` | one `filters.ts` module owns the allowlist; UI test pastes every generated URL back at the API |
| Phase 4 component set is missing a prop this phase needs | design language implementation contract: add the prop to `docs/design-language.md` first, coordinate with the Phase 4 owner via `hub` before touching shared files |
| Live announcements become noisy under load | announce counts only in one polite region; row-by-row announcements are prohibited by design language section 7 |

## Rollback

Phase 5 changes only `web/` and `tests/`: the server and API are untouched. Reverting the phase's
commits and rebuilding `web/out` restores the Phase 4 shell; alternatively the Observe nav entries
added to the shell can point back at the Phase 4 placeholder pages. The SSE client degrades to
polling on its own, so a stream regression never blocks the console. No schema or setting is
introduced, so no data cleanup is needed.

## Exit gate

Acceptance criteria 1 to 10 verified in the browser and in `tests/ui/test_observe_views.py`,
evidence recorded in `plan/phase-5-observability-views/evidence.md`, no open `blocker` or `major`
in `issues.md`, decisions recorded in `decisions.md` (at minimum: filter-to-URL mapping, SSE
reconnect and fallback policy, chart point handling), and `make test` green.
