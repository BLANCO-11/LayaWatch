# Phase 7 - catalog group 4.1 (request path, docs/performance.md section 4 items 1-5) - evidence

Status: REQUEST-PATH GROUP APPLIED 2026-09-23 (wave 2, phase B). Files changed (4.1 scope only):
`layawatch/app.py`, `layawatch/http/server.py`, `layawatch/store/db.py` (prepared-statement cache
only - the 4.3 pragma set, keyset predicates and indexes are untouched and re-verified below).
`router.py` was NOT needed by any item. No test file was edited; no catalog-vs-pinned-test conflict
was found, so nothing STOPPED. Every number is from my own harness runs on my own copy
`/tmp/lw-p7w2` (`cp -r /tmp/lw-bench`, 46M) with `LW_BENCH_PORT=8096`; the live `:8050` hub was
never touched; my smoke instance was stopped (graceful uvicorn shutdown in its log) and port 8096
was free afterwards.

## 1. Item-by-item: change -> verification

| # | Item | Change | Verification (this file) |
|---|---|---|---|
| 1 | Keep-alive plus `TCP_NODELAY` on the listening socket | `server.run` now pre-binds the listener (`_listening_socket`: `SO_REUSEADDR` + `TCP_NODELAY` + listen(2048), getaddrinfo like uvicorn's own bind) and hands it to `uvicorn.Server.run(sockets=[sock])`; `timeout_keep_alive=5` passed explicitly. Verified uvicorn 0.53 defaults against the catalog: keep-alive is already on by default (`timeout_keep_alive: int = 5` in Config and `uvicorn.run`), and **uvicorn sets `TCP_NODELAY` nowhere** (grep `TCP_NODELAY\|setsockopt` over `.venv/.../uvicorn`: only `SO_REUSEADDR`). | Inheritance probe: `listener nodelay: 1 / accepted nodelay: 1` on this kernel, so the catalog's literal wording (flag on the LISTENING socket) reaches accepted connections. Live smoke: two `GET /healthz` answered 200 on ONE client socket (same local port), header `Server: LayaWatch`, no `Connection: close`; bench `Client` is a single keep-alive `HTTPConnection` that never reconnects across 100-request series (zero `HarnessError`). Access log stays off -> no per-request log lines (catalog's "connection count in logs"). |
| 2 | Serialize the response once into `bytes` (no intermediate str plus re-encode) | Success and error now share ONE rendering point: `_render(resp, request_id, *, accept, background)` assembles headers once, applies the gzip decision once, and hands the body (already `bytes` from `json_response`/`text_response`) to the ASGI layer untouched - the success path's duplicate `dict(resp.headers)` + x-request-id scan + inline gzip block was deleted. `_Writer.write` no longer does `bytes(data)` on already-serialized bytes (that was one full copy per SSE frame); str is encoded exactly once. | Byte-exact pins: GREEN-29 `test_wire` (gzip round-trip `gzip.decompress(raw) == plain`, Content-Length exact, small/plain/healthz pins) passes unedited. Scope note: the one str->bytes serialization left is `json.dumps(...).encode()` at response BUILD time in `http/types.py` - outside this group's file list and unavoidable with stdlib json (no C deps, anti-goal); `_render`/`_gzipped` themselves never re-encode. |
| 3 | One-shot `read(length)` with a pre-checked `Content-Length`; reject oversized before reading | `app.py` `_read_body`: the existing pre-check (400 unparseable CL, 413 `declared > max` BEFORE any read, 411 CL missing) kept as-is; body read replaced `await asgi.body()` with a bounded pass - first `http.request` message returned as-is when it already carries the declared length (one-shot, no join), more messages pulled only while bytes are still owed, and undeclared/chunked bodies refused the moment the running total passes the cap (**new**: chunked bodies previously bypassed the cap entirely and buffered unbounded). | Live smoke on my own instance with `LAYWATCH_MAX_BODY=1024`: CL 4096 -> `413 payload_too_large` before read; no CL/TE -> `411 length_required`; chunked ~4 KB -> `413 payload_too_large` mid-read; small chunked -> body delivered whole (handler answered with its normal 400 state validation, not 411/413/hang). Pinned 411/413/400 framing tests pass unedited (GREEN-29 + full suite). |
| 4 | Cache per-thread prepared statements instead of re-preparing per request | `db.connect` returns a per-(thread, file) cached connection (`_ThreadConnections`, LRU cap 8 per thread, older entries really closed). The prepared-statement cache rides on pysqlite's per-connection `cached_statements=128`, which now survives `close()` across requests on a thread. `_CachedConnection.close()` keeps the old close CONTRACT for transactions - it rolls back open work - and only defers the handle release; `check_same_thread` (sqlite3 default) still pins a handle to its creating thread; `:memory:` is never cached (fresh per connect, sqlite3 contract). Pragma set extracted verbatim to `_configure` and applied to every new connection (4.3 item 11 unchanged). | Probe output: same thread+path -> `c2 is c1` reuse; live pragma read on a `db.connect` connection `journal_mode=wal synchronous=1 temp_store=2 cache_size=-16000 mmap_size=134217728 busy_timeout=5000 foreign_keys=1` (identical to 4.3's post-change read); uncommitted insert then `close()` -> row absent on reopen (rollback-on-close); other thread gets a different handle; cross-thread `execute` -> `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`; 9th path evicts oldest -> oldest `ProgrammingError` (really closed), cache size 8. Hot-path effect: `bench.py api` below - deep list p95 2.859 -> 2.350/1.998, metrics 1.771 -> 1.296/1.267. The full suite minus `test_real_adapter` (365 passed, 1 pre-existing environmental failure - section 4) plus `test_real_adapter` (23 passed) runs on this cache, including queries/writer/retention/migrations/api keys/logs/traces/metrics/settings/legacy and both groups' scoped tests. |
| 5 | Cache the gzip decision per `Accept-Encoding` value; skip compression under 1 KB | `_GZIP_ACCEPT` memo (max 64 entries, cleared past the bound - the header is client-controlled) via `_gzip_acceptable`; `_gzipped` now checks the 1 KB floor FIRST so small bodies never touch cache or gzip, then the memo, then content-type/encoding. The predicate is byte-identical (`"gzip" in accept`); the floor itself (`len(body) <= _GZIP_MIN`) already existed at baseline - reordered, not introduced. | GREEN-29 gzip pins byte-exact: large-json gzipped with correct Content-Length and `decompress(raw) == plain`, small JSON never (`raw == b'{"ok":true}'`, `Content-Length == 11`), html never (>1024 B, refused by content-type), healthz untouched, plain responses get no Content-Encoding/Vary. Memo probe: repeated identical accept strings return the same decision as the inline `in` scan. |

## 2. BEFORE/AFTER - my harness runs (`scripts/bench.py`, `LAYA_STATE_DIR=/tmp/lw-p7w2 LW_BENCH_PORT=8096`)

### overhead (`bench.py overhead`, p95 of record_on - record_off, budget <= 3.0 ms)

| When | overhead_added p95 | record_on p95 | Verdict |
|---|---|---|---|
| BEFORE (wave-1 end, given before-table) | **-0.451 ms** | - | PASS |
| after 4.2 (phase A, 3 runs: +0.492 / -0.024 / +0.172) | +0.492 / -0.024 / +0.172 ms | 2.397 / 1.538 / 1.570 | PASS x3 |
| **after 4.1 (phase B)** | **-0.010 ms** | 1.398 | PASS |

All runs pass; the series' run-to-run spread (~+/-0.5 ms) dominates every delta versus the given
before - the request-path group shows no regression and no measurable overhead win (it is not the
overhead metric's target; the wins land in `api`).

### api (`bench.py api`, two runs after 4.1; BEFORE = wave-1 end given table)

| Series | BEFORE | AFTER 4.1 run 1 | AFTER 4.1 run 2 | Delta |
|---|---|---|---|---|
| traces deep cursor @5000 p95 (budget 50 ms) | 2.859 ms | **2.350 ms** | **1.998 ms** | **-0.509 / -0.861 ms (-17.8 / -30.1 %)** |
| traces shallow p95 | not in before-table | 2.088 ms | 2.106 ms | after only (budget row = worst-of) |
| worst-of (budget verdict) | 2.859 PASS | 2.350 PASS | 2.106 PASS | PASS both |
| metrics_15m p95 (budget 30 ms) | 1.771 ms | **1.296 ms** | **1.267 ms** | **-0.475 / -0.504 ms (-26.8 / -28.5 %)** |

### static (`bench.py static`, two runs after 4.1; BEFORE = wave-1 end given table)

| Series | BEFORE | AFTER 4.1 run 1 | AFTER 4.1 run 2 |
|---|---|---|---|
| static_asset p95 (budget 5 ms) | 1.449 ms | 1.564 ms | 1.520 ms |
| conditional re-request | 304s pass | **100/100 returned 304** | **100/100 returned 304** |
| verdict | PASS | PASS | PASS |

Static delta is +0.07..+0.12 ms against the given before while the box also moved +/-0.1 ms between
my own consecutive runs (asset 1.564 -> 1.520, 304 1.133 -> 1.405): flat within noise, PASS with
large margin; static.py itself is outside this group's file list.

Raw JSONs: `bench/results/20260923-005810.json` (overhead), `...005815.json` + `...005856.json`
(api x2), `...005817.json` + `...005854.json` (static x2), plus phase A's three overhead files.

## 3. Commands + observed outputs (verification trail)

TCP_NODELAY inheritance + uvicorn defaults probe:

```
listener nodelay: 1
accepted  nodelay: 1
Server.run (self, sockets: 'list[socket.socket] | None' = None) -> 'None'
Config timeout_keep_alive: 'int' = 5 ; uvicorn.run timeout_keep_alive: 'int' = 5
```

db item 4 probe (throwaway): `cached handle reused: OK | rollback-on-close: OK | per-thread
isolation: OK | cross-thread execute: SQLite objects created in a thread can only be used in that
same thread ... | :memory: contract: OK | eviction: really closed | cache size: 8 | DB SMOKE OK`

Body framing smoke on my own instance (`LAYA_PORT=8096 LAYA_STATE_DIR=/tmp/lw-p7w2
LAYWATCH_MAX_BODY=1024`, engineless fake adapter):

```
A CL-oversize(fresh): 413 {"error":{"code":"payload_too_large","message":"request body exceeds 1024 bytes"}}
E no-CL:              411 {"error":{"code":"length_required","message":"Content-Length header is required"}}
D headers: Server=LayaWatch connection=None ; both 200s on one socket (client port 54078)
B chunked-oversize:   413 {"error":{"code":"payload_too_large", ...}}   # NEW: was unbounded
C chunked-small:      400 ... "'state' must be a JSON object"          # full body reached dispatch
BODY/KEEPALIVE SMOKE OK
```

Shutdown log (instance stopped): `Shutting down / Application shutdown complete / Finished server
process`; afterwards `ss -ltn` shows only the live `127.0.0.1:8050`.

## 4. Suites + lint (after all Phase B edits; no test file modified)

```
$ .venv/bin/python -m pytest tests/test_stream.py tests/test_wire.py tests/test_middleware.py -q
29 passed in 9.29s            # GREEN-29 preserved after phase B as well

$ .venv/bin/python -m pytest tests/test_recorder.py tests/test_rollup.py \
      tests/test_redact.py tests/test_vocabulary.py -q
53 passed in 0.16s            # phase A scoped suites still green

$ .venv/bin/python -m pytest tests/ -q --ignore=tests/test_real_adapter.py
1 failed, 365 passed in 21.63s
$ .venv/bin/python -m pytest tests/test_real_adapter.py -q
23 passed in 0.24s

$ .venv/bin/ruff check layawatch/app.py layawatch/http/server.py layawatch/store/db.py \
      layawatch/obs/recorder.py layawatch/log.py
All checks passed!
```

The single failure is PRE-EXISTING and environmental, not caused by this group:
`test_legacy_state.py::test_real_repo_file_imports_zero_keys_and_renames` needs the repo-root
fixture `api_keys.json`, which is ABSENT - it was renamed to `api_keys.json.imported`
(`-rw------- ... Sep 22 10:55`, ~14 h before my session; the legacy import performs that rename on
whatever state dir boots against the repo root - i.e. the live deployment consumed it). I did not
touch either file (live-box territory); the other 5 tests in that file pass on the new db cache.

## 5. Revert-coupling notes (for wave 3 keep/revert)

- Item 1: self-contained in `server.run` + `_listening_socket`; revert restores `uvicorn.run(...)`.
  Keep-alive was already on (uvicorn default 5 s) either way - only TCP_NODELAY is new behavior.
  Port-in-use still raises `OSError` at pre-bind, so harness spawn retry loops are unaffected.
- Item 2: `_render` consolidation + `_Writer.write` pass-through; revert splits the two render
  paths again. Byte-exactness is pinned by test_wire in both shapes (behavior-neutral refactor,
  the measurable part is the removed `bytes(bytes)` copy on the stream path).
- Item 3: `_read_body` + `declared` typing in `handle`; revert is `body = await asgi.body()`,
  which re-opens the unbounded-chunked hole. The pre-read 413/411/400 pre-checks predate the
  group and stay either way.
- Item 4 (the only semantic-contract change): `close()` = rollback + keep handle. Couplings:
  (a) every suite above runs on the cache - revert is `db.py` only, queries/writer/retention
  untouched; (b) WAL is no longer auto-truncated by last-connection close while a server lives -
  disk stays bounded by 4.3's idle `wal_checkpoint(TRUNCATE)` (item 17, runs every retention pass,
  works with open connections); `budget_check.py disk` re-measures at the final sweep (Main);
  (c) steady-state RSS holds one connection per worker thread (page cache budget `-16000`,
  reads served from the 128 MB mmap) - `budget_check.py rss` is Main's final-sweep number;
  (d) `seed.py` prints `file_bytes` BEFORE `close()` (verified: seed.py:365-366 inside the try),
  so its published wal=0 evidence line is unchanged.
- Item 5: memo is a pure-function cache over the exact old predicate; revert = inline
  `"gzip" in accept` at the same call site. Floor behavior identical (was already present).
- Cross-group: `bench.py` AFTER numbers above are this group's deliverable; phase-final sweeps
  (task 10) re-run everything including `sse`, `throughput` and the four budget checks.
