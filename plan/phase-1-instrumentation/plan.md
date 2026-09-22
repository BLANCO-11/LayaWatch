# Phase 1 - Instrumentation and storage

Status: planned
Depends on: phase 0
Estimated effort: 4 to 6 days
Deliverable: every `/predict` and `/route` call becomes a trace with spans, persisted to SQLite,
rolled up for charts, pruned by retention, with measured overhead inside budget.

## Objective

Build the recorder, the span vocabulary and the storage pipeline, then wire them into the engine
request path so a real laya call produces an inspectable trace.

## Scope

In: span vocabulary, recorder and rings, redaction, writer thread, rollups, retention, log capture,
engine adapter over the real `laya` package, legacy `api_keys.json` import, instrumentation of both
engine endpoints, overhead measurement.

Out: HTTP API resources for reading traces (Phase 2), SSE (Phase 2), auth and users (Phase 3), any UI.

## Deliverables

```
layawatch/obs/vocabulary.py    canonical span names, attribute schemas, status rules
layawatch/obs/recorder.py      trace context, span timers, trace assembly, rings, sampling
layawatch/obs/redact.py        key-based redaction, truncation, control-char stripping
layawatch/obs/rollup.py        10s/1m/1h bucket aggregation and percentile computation
layawatch/store/writer.py      queue, batching, transactions, backpressure, drop counters
layawatch/store/queries.py     insert and read helpers used by writer and later phases
layawatch/store/retention.py   prune jobs, rollup cascade, WAL checkpoint
layawatch/engine/adapter.py    real adapter: Router/Agent, _predict_lock, model lifecycle, warmup
layawatch/engine/keys.py       API key verify path used by middleware (issue/rotate lands in Phase 3)
layawatch/engine/legacy_state.py  one-time api_keys.json import, renames the file when done
tests/                         recorder, rollup, redaction, retention, writer, engine adapter
scripts/smoke_laya.py          extended to assert a trace was recorded for a real request
scripts/budget_check.py        rss, disk, coldstart, idle-cpu subcommands
```

## Tasks

1. `vocabulary.py`: implement the span table from `docs/observability-model.md` section 2 with typed
   attribute validation; unknown span names fail a test, not production.
2. `recorder.py`: per-request trace context (thread-local), `span(name, **attrs)` context manager,
   monotonic timers, trace assembly with totals, ring buffers for traces and logs, sampling decision
   (`LAYWATCH_TRACE_SAMPLE`, errors and refusals always kept).
3. `redact.py`: recursive key redaction list, truncation to `LAYWATCH_PAYLOAD_MAX`, control-character
   stripping, byte-size accounting that never stores content when capture is off.
4. `store/writer.py`: bounded queue, batch of 200 rows or 250 ms, single transaction per batch,
   `traces` + `observations` + `scores` inserts, rollup updates, retry with backoff on `SQLITE_BUSY`,
   drop-oldest-non-error policy, counters (`write_queue_depth`, `write_latency_ms`, `obs_dropped_total`).
5. `store/retention.py`: 60 s timer applying trace and log caps, cascading 10 s rollups into 1 m and
   1 m into 1 h, deleting expired payloads after 24 h, `PRAGMA wal_checkpoint(TRUNCATE)` when idle,
   and a `VACUUM` guard that only runs when free pages exceed 20 % of the file.
6. `engine/adapter.py`: wrap `laya.Router` and `laya.Agent` exactly as `legacy/serve.py` does
   (preload list, `ENGLISH_ONLY` pop, `_predict_lock`, `_rss_mb`), emit `lang.detect`, `route.decide`,
   `queue.wait`, `model.load`, `forward`, `serialize` spans, expose `load`/`unload` with a single
   in-flight guard and an available-memory check.
7. Instrument `POST /predict` and `POST /route` through middleware: `http.receive`, `auth.verify`,
   `body.parse`, `error`, `response.send`; set `X-Request-Id` from the trace id.
8. `engine/legacy_state.py`: import `api_keys.json` (enabled flag plus keys with their HMAC hashes)
   into `api_keys` and `settings`, then rename the file to `api_keys.json.imported`; idempotent.
9. Log capture: route every `log.py` line into the ring and the writer, with `trace_id` attached when
   inside a request context.
10. Tests: span assembly ordering, sampling behavior, redaction fixtures, rollup percentile math
    against a hand-computed sample, retention cascade, writer backpressure, legacy import idempotency,
    adapter behavior with a stub engine (no torch).
11. `scripts/budget_check.py`: implement the four subcommands; run them here to catch regressions
    early even though the gate is Phase 7.
12. `scripts/smoke_laya.py`: after the real request, assert a trace row exists with `status = 200` and
    at least 8 observations, and print the trace id.

## Acceptance criteria

1. One real `/predict` against the english checkpoint produces a trace with `http.receive`,
   `auth.verify`, `body.parse`, `lang.detect`, `route.decide`, `queue.wait`, `forward`, `serialize`,
   `response.send`, with `duration_ms` equal to the sum of top-level spans plus stated overhead.
2. `queue_ms` is non-zero when two requests are issued concurrently against a single-worker engine and
   zero for a single request on an idle engine.
3. A 422 non-English rejection is recorded as a trace with `status = 422`, an `error` event, and no
   `forward` span, and it is never sampled away.
4. Rollups for `requests`, `latency`, `queue` are queryable within one tick (10 s) of a request.
5. Retention prunes to the configured caps; after pruning, 1 m and 1 h rollups still answer the same
   window, and a chart query returns no gaps for pruned periods.
6. Overhead: with recording on, p95 latency increases by no more than 3 ms versus recording off
   (measured over 200 requests, same payload, warm engine).
7. RSS overhead after warmup is <= 120 MB over the engine-only baseline.
8. `api_keys.json` is imported once; a second start does not duplicate keys and logs an info line.
9. Payload capture off by default: no `input`/`output` columns contain content, verified by a test
   that greps the database file for a fixture marker string.

## Evidence required

- `scripts/smoke_laya.py --router` output including the trace id, plus `sqlite3` dump of that trace and
  its observations.
- Overhead table: recording off vs on, 200 requests, p50/p95/p99.
- `budget_check.py rss` and `disk` output.
- Rollup query output before and after a retention run.
- Concurrency run showing `queue.wait` populated with two parallel requests.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Recorder overhead distorts the latency it measures | measure overhead explicitly (criterion 6); keep spans to two `perf_counter` calls and dict writes |
| Writer thread starves under bursts | batch sizing plus drop-oldest policy; assert counters move under a synthetic burst test |
| Percentile math disagrees with the UI expectation | unit tests against hand-computed fixtures; the UI states that percentiles are bucket-level |
| Model load/unload thrash | single in-flight guard, RAM check, audit entry (audit table exists from Phase 0 schema) |
| Legacy import corrupts an existing deployment | import runs in one transaction, renames the source only after commit, and is tested against a copy of a real `api_keys.json` |

## Rollback

Recording is behind `LAYWATCH_TRACE_SAMPLE` and a feature flag (`LAYWATCH_RECORD=0` disables the
recorder entirely). Disabling it restores pre-Phase-1 latency behavior without a code change. The
legacy import is one-way; the renamed file plus a fresh database are the documented recovery path.

## Exit gate

Acceptance criteria 1 to 9 verified, evidence recorded, decisions recorded for the write pipeline,
sampling policy and legacy import.
