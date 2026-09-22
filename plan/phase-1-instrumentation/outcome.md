# Phase 1 outcome

Status: complete (2026-09-22). Gate: acceptance criteria 1 to 9 verified; see `evidence.md`.

## What shipped

- `layawatch/obs/`: `vocabulary.py` (typed span table, unknown spans fail tests), `recorder.py`
  (thread-local contexts, span/event timing, sampling with the D-002/D-011 rules, 1000-trace ring,
  inert `enabled=False` fast path, thread-local spans proxy support), `redact.py` (key redaction,
  UTF-8-safe truncation, control-char stripping), `rollup.py` (bucketing, nearest-rank percentiles,
  counter/distribution summarize, weighted row merge).
- `layawatch/store/`: `writer.py` (one thread, bounded queue, 200-row/250 ms batches, single
  transaction, drop-oldest-non-error backpressure, 10 s bucket close with 60 s/1 h cascade, log
  rows, counters), `queries.py` (inserts, rollup upserts, newest/count readers), `retention.py`
  (pending-rollup build before prune, chunked trace/log deletes, payload TTL nulling, session
  pruning, idle WAL checkpoint, >20 % freelist VACUUM guard; `apply()` returns per-action counts).
- `layawatch/engine/`: `RealAdapter` (legacy-faithful Router/Agent wrapping, lazy laya import,
  `_predict_lock` serialization observed as `queue.wait`, engine spans, `EnglishOnlyError`,
  `EngineMemoryError`, single-in-flight load guard), `keys.py` (pepper, dual-format verify,
  `keys.auth` arming flag), `legacy_state.py` (one-time `api_keys.json` import with rename).
- `layawatch/http/middleware.py` + `layawatch/api/engine.py` + server `dispatch`/`on_sent` hooks:
  the architecture section 4 lifecycle (`http.receive`, `auth.verify`, `body.parse`, engine spans,
  `response.send` over the socket write, error events, trace close), `POST /predict` and
  `POST /route`, X-Request-Id == trace id, non-engine paths untouched.
- `layawatch/__main__.py` wires config -> migrate -> legacy import -> writer + log sink ->
  recorder + trace provider -> retention thread -> adapter (real/fake probe) -> routes ->
  instrumented dispatch -> static, with ordered shutdown.
- Tooling: `scripts/budget_check.py` (rss, disk, coldstart, idle-cpu, all, --json; all four pass
  against the live box), `scripts/smoke_laya.py` extended with the HTTP trace phase (spawns a
  server, asserts the persisted trace, prints every observation name).
- Tests: 254 passing, ruff clean (Phase 0's 107 plus recorder53/writer-retention-queries32/
  engine55-middleware-engine-api22 with overlap from combined runs).

## What did not ship (by scope)

- Read API for traces/metrics/logs and the SSE stream (Phase 2); any auth UI, user management,
  arm/disarm endpoint (Phase 3 - only the settings flag and verifier exist); score capture from
  the playground (Phase 6); `forward`/`rss` rollup metrics deferred (observability-model lists
  them; schema and pin named four metrics - see P1-D11).

## What Phase 2 inherits

- `Writer.on_flush(list_of_kept_traces)` fires after every commit - the SSE broadcast hook.
- Recorder rings (`ring_traces`, `log.ring_entries`) as the live/replay source; `Last-Event-ID`
  replay reads the trace ring (200-event bound per api-reference section 8).
- `store/queries.py` as the home for list/filter/cursor queries (it currently holds inserts and
  rollup helpers only).
- `instrument(...)` already renders every error as the section 2 envelope with stable codes
  (P1-D4) - API resources raise `HttpError` and inherit the same recording path.
- Auth verifier (`verify_key`, `auth_armed`, `keys.auth`) for session login in Phase 3.
- Open issues: P1-I1 (smoke child death, under investigation), P1-I2 (engine schema errors map to
  500), P1-I3 (one engine per host RAM note for operations.md).
