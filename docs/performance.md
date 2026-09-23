# LayaWatch performance and optimization

Version: 0.1 (2026-09-22)
Rule of this document: **measure first, then optimize, then prove it with the same measurement.**

## 1. Budgets

| Budget | Target | Harness |
|---|---|---|
| LayaWatch RSS overhead (no model weights) | <= 120 MB | `scripts/budget_check.py rss` |
| LayaWatch disk at 10k traces | <= 200 MB | `scripts/budget_check.py disk` |
| Cold start to first served request | <= 3 s (excluding model load) | `scripts/budget_check.py coldstart` |
| Idle CPU with no clients | < 2 % over 60 s | `scripts/budget_check.py idle-cpu` |
| Production image delta over `python:3.12-slim` + torch | <= 50 MB | `scripts/budget_check.py image` |
| Recording overhead on engine latency | <= 3 ms p95 added | `scripts/bench.py overhead` |
| Trace list query, 10k traces | <= 50 ms p95 | `scripts/bench.py api` |
| Metrics query, 15 m window | <= 30 ms p95 | `scripts/bench.py api` |
| Static asset response | <= 5 ms p95, `304` when unchanged | `scripts/bench.py static` |
| SSE tick cost with 20 clients | <= 5 ms per tick | `scripts/bench.py sse` |

The engine itself (torch CPU forward pass) dominates latency and is out of scope for optimization
except where LayaWatch can avoid work (see 4.12 to 4.14).

## 2. Measurement harness

- `scripts/bench.py` subcommands: `overhead`, `throughput`, `api`, `static`, `sse`, `all`. Each prints
  a table (p50, p90, p95, p99, max, n) and writes JSON to `bench/results/<timestamp>.json` for
  comparison across phases.
- `scripts/budget_check.py` asserts every resource budget (`rss`, `disk`, `coldstart`,
  `idle-cpu`, `image`) and exits non-zero on failure.
- Both run against a seeded database (`scripts/seed.py --traces 10000 --observations 9`).
- Run `bench.py overhead` and `bench.py api` before a release with loose thresholds (2x budget) to
  catch regressions, and `budget_check.py` for the hard resource gate.
- Baseline numbers are recorded in `plan/phase-7-optimization/evidence.md` before any change, so every
  optimization has a before and after.

## 3. Anti-goals

- No caching layer that can serve stale correctness-critical data (no in-memory duplicate of traces).
- No C extension, no Cython, no compiled helper for the v0.1 line.
- No micro-optimization without a measurement showing the win.
- No optimization that weakens a documented guarantee (durability, auth, retention).

## 4. Optimization catalog

Ordered by expected impact for this workload. Each entry states the change, the expected effect, and
how it is verified.

### 4.1 Request path

| # | Change | Expected | Verify |
|---|---|---|---|
| 1 | Keep-alive plus `TCP_NODELAY` on the listening socket; reuse the connection for the UI's burst of asset requests | fewer syscalls, lower TTFB for the UI | `bench.py static`, connection count in logs |
| 2 | Serialize the response once into `bytes` (no intermediate `str` plus re-encode) | removes one full copy per response | `bench.py overhead` |
| 3 | Read the request body in a single `read(length)` with a pre-checked `Content-Length`; reject oversized before reading | avoids chunked read loops and large-body memory spikes | unit test plus `bench.py overhead` |
| 4 | Cache per-thread prepared SQLite statements instead of building SQL strings per request | removes parse cost on hot reads | `bench.py api` |
| 5 | Cache the gzip decision per `Accept-Encoding` value and skip compression under 1 KB | avoids wasted work on small JSON | `bench.py api` |

### 4.2 Recorder

| # | Change | Expected | Verify |
|---|---|---|---|
| 6 | `__slots__` on `Trace` and `Span`; one attribute dict per span built from the vocabulary schema | fewer allocations per request (9 spans) | `bench.py overhead`, `tracemalloc` snapshot |
| 7 | Timers via two `time.perf_counter()` calls per span, no `datetime` | avoids object construction in the hot path | code review plus overhead bench |
| 8 | Fixed-size `deque` rings, no copying when handing data to SSE (read by index snapshot) | bounded memory, no per-tick copies | `bench.py sse` |
| 9 | Skip log-line formatting entirely when the level is filtered out | removes formatting cost for `debug` lines in production | unit test plus overhead bench |
| 10 | Sampling decision made once per trace, before span assembly | cheap rejection for sampled-out successes | unit test |

### 4.3 Storage

| # | Change | Expected | Verify |
|---|---|---|---|
| 11 | SQLite pragmas: `journal_mode=WAL`, `synchronous=NORMAL`, `temp_store=MEMORY`, `cache_size=-16000`, `mmap_size=134217728`, `busy_timeout=5000` | fewer fsyncs, faster reads | `bench.py api`, `budget_check.py disk` |
| 12 | Batch inserts of 200 rows or 250 ms in one transaction, `executemany` | amortizes transaction cost | `bench.py overhead`, writer latency counter |
| 13 | Covering indexes for the exact list queries in `docs/api-reference.md` (`traces(ts_start DESC)`, `(status, ts_start)`, `(route, ts_start)`, `(model, ts_start)`) | index-only scans for list views | `EXPLAIN QUERY PLAN` recorded in evidence |
| 14 | Keyset pagination only, never `OFFSET` | constant cost on deep pages | `bench.py api` with a cursor deep in the table |
| 15 | Rollups computed in the writer tick and at 1 m and 1 h by retention, so charts never scan `traces` | chart queries stay under 30 ms after pruning | `bench.py api`, `budget_check.py disk` |
| 16 | Chunked retention deletes (500 rows per statement, loop until caught up) | avoids long write locks | retention log lines, writer latency during prune |
| 17 | `wal_checkpoint(TRUNCATE)` when idle, `VACUUM` only above 20 % free pages | keeps the file compact without surprise stalls | `budget_check.py disk` |
| 18 | `PRAGMA optimize` after bulk writes and on shutdown | keeps the planner honest | query plan evidence |

### 4.4 Engine interaction

| # | Change | Expected | Verify |
|---|---|---|---|
| 19 | `torch.inference_mode()` around every forward pass and `torch.set_num_threads(min(cores, 8))` | removes autograd bookkeeping, avoids BLAS oversubscription | `bench.py throughput`, CPU utilization |
| 20 | One warmup pass per checkpoint at startup | removes first-request outlier (tokenizer, lazy allocs) | cold-start evidence |
| 21 | Skip `lang.detect` when the client pins `lang=`, and skip `route.decide` when `model=` is explicit | saves the detection pass on pinned requests | span evidence (spans absent, documented) |
| 22 | Background prefetch of the next checkpoint only when `LAYA_MODELS` lists it and idle CPU is available | hides load latency without thrash | model event log, RAM guard |
| 23 | Reuse the tokenizer object per checkpoint (already loaded) and avoid re-reading config files per request | removes file I/O from the hot path | `strace -c` comparison in evidence |

### 4.5 Streaming

| # | Change | Expected | Verify |
|---|---|---|---|
| 24 | One coalesced payload per SSE tick instead of per-trace writes; diffs only | fewer writes, stable tick cost | `bench.py sse` |
| 25 | Heartbeat only when the socket has been idle for 15 s | avoids needless traffic | `bench.py sse` |
| 26 | Drop dead clients on write error instead of retrying | no zombie connections accumulating | unit test on the registry |

### 4.6 Frontend

| # | Change | Expected | Verify |
|---|---|---|---|
| 27 | Static export, no runtime Node, no chart library, hand-built SVG | small bundle, no hydration of chart libs | bundle size report |
| 28 | Route-level code splitting; the trace detail view and playground load on demand | smaller first load | Next.js build output |
| 29 | SSE replaces polling when connected; polling is the fallback only | fewer requests, lower idle CPU | network panel, `bench.py sse` |
| 30 | Skeletons instead of spinners; `content-visibility: auto` on long tables; logs virtualized beyond 500 lines | keeps the UI responsive at 10k rows | manual check with seeded data |
| 31 | Precompressed `.br`/`.gz` assets generated at build time and served directly | no runtime compression cost | `bench.py static` |

## 5. Execution order in Phase 7

1. Baseline: run every harness, record numbers in `evidence.md` (no changes yet).
2. Apply 4.3 (storage) and 4.2 (recorder): highest impact on the metrics that matter and cheapest.
3. Apply 4.1 and 4.4, re-running `overhead` and `throughput` after each group.
4. Apply 4.5 and 4.6, re-running `sse` and measuring bundle size.
5. Re-run all harnesses, compare against baseline, and record the delta table.
6. Keep only changes that show a measurable win or a clear robustness gain; revert the rest and say so.

## 6. Regression guards

- `bench.py` JSON results are committed per phase so drift is visible in review.
- `bench.py --strict` fails when a series exceeds 2x the budget (noisy environments tolerated);
  `budget_check.py` is the hard gate at release.
- Any change to the recorder, writer or query layer must include before/after numbers in the phase
  `evidence.md`. "Should be faster" is not evidence.
