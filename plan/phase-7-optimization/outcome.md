# Phase 7 - Performance and optimization: outcome

Status: COMPLETE 2026-09-23 (wave 3). All nine `docs/performance.md` section 1 budgets PASS at
the final sweep; baseline + per-group + AFTER plans + final delta table live in `evidence.md`.

## Keep or revert (plan task 11 - rule D-009 / R-10: measured win or clear robustness gain keeps;
anything unmeasured reverts)

**Result: keep all 30 applied/verified catalog items, reject 1 (item 25, DEC-1), revert 0** -
plus 6 non-catalog changes kept (all measured). Every row carries its number; sources are
`evidence.md` (merged) and the per-group fragments `evidence-4.{1..6}.md`.

### Catalog items 1-31

| # | Item | Disposition | Measured number (or proof) |
|---|---|---|---|
| 1 | Keep-alive + TCP_NODELAY listener | KEEP | nodelay inherited `listener 1 / accepted 1`; uvicorn 0.53 has no TCP_NODELAY of its own; static final 1.531 ms PASS |
| 2 | Single `bytes` response serialization | KEEP | one duplicate render path + one `bytes(bytes)` copy per SSE frame removed; GREEN-29 wire pins byte-exact; overhead final -0.378 ms |
| 3 | One-shot `read(length)` body, reject oversized | KEEP | smoke: 413-before-read / 411 / 400; NEW chunked-oversize 413 closed an unbounded-buffer hole (robustness) |
| 4 | Per-thread prepared-statement cache | KEEP | deep cursor p95 5.277 -> 2.018 ms final (wave-2 best 1.998); probe: reuse OK, rollback-on-close contract kept |
| 5 | Gzip-decision memo + 1 KB floor | KEEP | metrics_15m 1.771 -> 1.267 ms (wave-2); gzip pins byte-exact in GREEN-29 |
| 6 | `__slots__` on Trace/Observation | KEEP | tracemalloc 1847040 -> 1669496 B retained (-177544 B, -9.6 %) |
| 7 | 2x `perf_counter`/span, no datetime | KEEP (already compliant) | probe: exactly 4 calls per start+span+finish; no `datetime` token in recorder.py |
| 8 | Index-snapshot ring reads (no full copy) | KEEP | semantics == pinned contract; sse final 2.445 ms with 20/20 fed |
| 9 | Skip formatting of filtered log lines | KEEP (already compliant) | probe: hidden debug emits nothing; `info` still writes "s shown-line" |
| 10 | Sampling decision first, per trace | KEEP | overhead ladder +0.492 / -0.024 / +0.172 ms, all PASS <= 3 ms; one draw/trace keeps RNG order |
| 11 | Full SQLite pragma set on every connection | KEEP | live read: WAL/NORMAL/MEMORY/-16000/128M/5000/foreign_keys=1; rss 74.5, disk 46.1 PASS |
| 12 | Batched executemany inserts | KEEP (already compliant) | `Writer(batch_rows=200, batch_ms=250)` pre-existing; writer tests green |
| 13 | Covering indexes (migration 0003) | KEEP | all nine AFTER plans: zero `USE TEMP B-TREE`; traces worst 5.729 -> 4.094 ms final |
| 14 | Row-value keyset, never OFFSET (lists) | KEEP | deep@5000 plans one `SEARCH ((ts_start,id)<(?,?))`; deep p95 5.277 -> 2.018 ms final |
| 15 | Writer-tick + 1m/1h rollups | KEEP (already compliant) | metrics plan rollup-only, no `traces` scan; metrics 2.309 -> 1.178 ms final |
| 16 | Chunked retention deletes (500) | KEEP | probe: `{'traces': 3, 'logs': 2}` DELETEs, 10 commits for 1200+600 rows, no long write lock |
| 17 | Idle `wal_checkpoint(TRUNCATE)` + 20 % VACUUM guard | KEEP (already compliant) | WAL 0 bytes after sessions; `test_retention` covers idle-vs-busy + guard |
| 18 | `PRAGMA optimize` after writes/shutdown | KEEP | `sqlite_stat1` populated (`traces_ts: 10000 1 1`); plans captured with stats present |
| 19 | `inference_mode` + threads=min(cores,8) | KEEP | driver: 4/4 spied forwards inference_mode, `torch.get_num_threads()==4` |
| 20 | One warmup pass per checkpoint | KEEP | startup logs `warmed english in 524 ms` / `385 ms`; stub: exactly one warm, never re-warms |
| 21 | Skip `lang.detect`/`route.decide` when pinned | KEEP | 3 HTTP traces: `lang.detect` absent with lang=de, `route.decide` absent with model=english; 30 stub assertions; documented in observability-model.md |
| 22 | Gated background prefetch | KEEP | gates observed: membership + 1-min loadavg + MemAvailable (defer log `1.5 GB < 1.5 GB`); unload cancels queue |
| 23 | Tokenizer/config reuse | KEEP (already compliant) | strace: 0 `openat` during 4 predicts vs 9249 during load (capture above) |
| 24 | One coalesced payload per SSE tick, diffs only | KEEP | 10 events -> one 866-byte write; sse ladder 69.031 -> 18.017 -> 14.906 -> 2.437 -> 1.196 -> **2.445 ms final PASS** |
| 25 | Heartbeat only after 15 s socket-idle | **REJECT** | never applied - contradicts pinned test + api-reference section 8; see DEC-1 |
| 26 | Drop dead clients on write error | KEEP | RST'd client removed within one write error (10 s window); no retry path exists; live publishes stay silent |
| 27 | Static export, no chart library | KEEP (confirmed) | 0 chart markers in 26 JS files; worst gzip 71459 B; bundle gate green |
| 28 | Route-level code splitting | KEEP (confirmed) | 16 routes, 22 chunks = 8 shared + 14 route; detail chunk fetched on demand at runtime |
| 29 | SSE with polling fallback only | KEEP (confirmed) | stream abort -> poll trio after 2 failures; recovery -> `live`, 0 further polls |
| 30 | Skeletons + content-visibility + log virtualization | KEEP | content-visibility applied (>100 rows, 45 px, 0 zero-height); 600 log lines -> 81 DOM nodes |
| 31 | Precompressed .br/.gz served directly | KEEP | 93 .br + 93 .gz, 712660 B; static 1.531 ms PASS, 100/100 304, `content-encoding: br` observed |

### Non-catalog changes kept (all measured)

| Change | Where | Measured number |
|---|---|---|
| Per-stream producer daemon thread (replaces `run_in_executor(None, ...)`) | `app.py` (4.5 baseline fix) | 8-worker executor cap removed: data-fed 8/20 -> 20/20 |
| Hub capacity 16 -> 20 | `stream.py` (4.5 baseline fix) | baseline 4x `503 sse_unavailable` -> 0 rejected, 21st still 503 |
| Midpoint timer prefetch of the pulse frame | `stream.py` (wave-2.5 part 1) | convoy removed; alone 17.511 (diagnosed no-move), prerequisite for final 1.196/2.445 |
| Loop-native writer handoff (`call_soon_threadsafe` event) | `app.py` (wave-2.5 part 2) | the SSE win: 17.511 -> 2.437 ms p95 |
| Single epoll stamping thread replacing 20 reader threads | `scripts/bench.py` (wave-2.5 part 3) | p95 base 2.077 -> 0.925 p50; GIL straggler tail gone (standalone stamp jitter p50 0.785 / max 3.288 ms) |
| `build_server()` test-fixture restoration | `http/server.py` (4.5) | GREEN-29 collectable again; 29 passed after every wave-2.5 part |

Not kept: wave-2.5 part 4's TEMP instrumentation (produce/send/arrival stamps) ran exactly one
diagnostic run (20260923-013042) and was removed - grep for `sse-stamp (write|send)|_SendStamp|
stamped_send|sse_diag|TEMPORARY|arrival dump` over `app.py`/`bench.py`/`stream.py` returns no
matches. Nothing unmeasured was found in the wave-3 walk, so nothing was reverted.

## Decisions

### DEC-1: catalog item 25 REJECTED (literal "socket-idle 15 s heartbeat")

- Date: 2026-09-23. Status: accepted (closes the wave-1 STOP report).
- Context: item 25 says heartbeat only when THE SOCKET has been idle for 15 s. The pinned test
  `test_hello_first_with_exact_headers_then_pulse_and_ping` (monkeypatched `_PING_S = 0.3`,
  `tick_s = 0.2`) asserts wire order `hello -> pulse -> ping` within 3 s, and
  `docs/api-reference.md` section 8 documents "ping - every 15 s to keep proxies open".
- Decision: REJECT the literal reading. Keep the documented `_PING_S = 15` cadence; code and
  docs already agree, so no test or doc edit is needed.
- Rationale: under the literal reading a ping can only fire after 15 s of socket-idle, but the
  3 s pulse writes every tick - the write gap never reaches 15 s while pulses flow (test's own
  cadence: 0.2 s gap < 0.3 s -> ping never fires -> `AssertionError: no SSE event within 3.0 s`;
  production: tick 3 s < 15 s -> no ping would EVER be sent), which also falsifies api-reference
  section 8. The two candidate "compatible" readings (idle = time since last trace/log write; or
  ping only on an empty wake) invent semantics the catalog does not state - prohibited.
- Alternatives: (a) accept literal -> must evolve the pinned test and api-reference section 8;
  (b) accept literal with reinterpreted "idle" -> semantic invention; (c) reject, keep cadence.
- Consequences: observed under the retained cadence: first ping 15.0-15.0 s after hello across
  20/20 clients under continuous pulses; the 3 s pulse already keeps proxies alive and the
  ping still fires when the stream stalls (and when `tick_s` is configured > 15 s).
- Evidence / links: `evidence-4.5.md` "Item 25 conflict report"; GREEN-29 unedited and green.

### DEC-2: pulse-age semantics for the wave-2.5 prefetch (midpoint compute)

- Date: 2026-09-23. Status: accepted.
- Context: part 1 arms a daemon timer after the frame claim so later tick crossings find a
  frame younger than `tick_s` (kills the compute/wait convoy). The refresh computes the
  payload at the window midpoint, not at the crossing.
- Decision: pulse data age at delivery becomes up to `tick_s/2 + provider time` (provider
  p50 0.553 / max 0.682 ms on the seeded db) instead of ~P. This is within documented
  behavior: api-reference section 8 documents one pulse per `tick_s`, and trace/log delivery
  is already up-to-one-tick (the playground docstring says "within one stream tick").
- Alternatives: recompute on every crossing (the convoy being removed - rejected), or change
  the documented cadence (docs edit outside the closed catalog - rejected).
- Consequences: noted in `stream.py` and `_tick_batch` docstrings; cost is the bounded
  half-tick age, benefit is 69.031 -> 2.445 ms p95 (final) with 20/20 fed.
- Evidence / links: `evidence-4.5.md` wave-2.5 part 1.

### DEC-3: epoll stamping methodology (wave-2.5 part 3) and SSE_SKIP_WAVES=2 retained

- Date: 2026-09-23. Status: accepted.
- Context: 20 per-client reader threads stamped arrivals; their GIL arbitration produced a
  1-3 ms straggler tail (standalone stamp jitter p50 0.785 / max 3.288 ms), and thread-start
  stamps sat far from actual socket arrival.
- Decision: one stamper thread on a `selectors.DefaultSelector` over all 20 fds, single
  `time.monotonic()` clock, stamps on readable, unregisters on EOF (level-triggered epoll
  cannot spin); the connect thread stamps the header-read remainder (closest to arrival);
  `run_sse` starts the stamper before connecting and joins before closing sockets.
  `SSE_SKIP_WAVES = 2` RETAINED on re-check: wave 0 carries the one-time cold frame claim
  (+0.4 ms) and per-wave write spreads are flat after it (14.085, 13.611, 13.613, 13.593, ...)
  - there is no spin-up gradient, so waves 0-1 are the right exclusion and no wave was
  excluded to flatter the number.
- Alternatives: keep 20 threads (tail persists), per-thread sequence stamps (phase drift
  across clocks), skip more waves (would flatter - rejected).
- Consequences: base halved (p95 2.437 with p50 2.077 -> p95 2.446 with p50 0.925); the
  one-run part-4 diagnostic then proved the residual 14.180 was a single connect-phase
  hello-lag outlier client, and its instrumentation was removed (grep no matches).
- Evidence / links: ladder rows 3-5 in `evidence.md`; `evidence-4.5.md` parts 3-4.

### DEC-4: keep-all ruling (plan task 11)

- Date: 2026-09-23. Status: accepted.
- Decision: all 30 applied/verified catalog items + 6 non-catalog changes KEPT; 0 reverted;
  item 25 rejected per DEC-1. Every recorder/writer/query change carries explicit before/after
  numbers in `evidence.md`; no unmeasured change was found in the walk (D-009, R-10).

## BACKLOG (plan task 13, R-11: ideas outside/beyond the closed catalog - never implemented
in phase 7; one-line rationale each)

1. SseDiag2 budget-realism note (history://SseDiag2) - **RESOLVED / moot**: SSE now 2.445 ms
   (final) <= 5, no budget or doc change needed; recorded here as closed.
2. Stream-status pill alternation (P7Front): label flips `polling 3s` / `reconnecting` during
   an outage while polls keep firing - cosmetic wording, zero budget impact.
3. Sidebar prefetch observation (P7Front): Next prefetches visible sidebar links, warming some
   route chunks idle - intended framework behavior, still absent from the initial document's
   references; no action needed.
4. Engineless harness cannot observe group 4.4 (P7Engine, R-11): `bench.py` boots
   FakeAdapter; a real-engine throughput number needs a harness change - defer; until then the
   smoke/stub/strace driver numbers in `evidence-4.4.md` are the engine evidence.
5. Item-25 STOPPED conflict (P7Stream) - **closed by DEC-1** (rejected); listed for the
   record: pinned test and api-reference section 8 stand unchanged.
6. Stream capacity config knob (`LAYA_STREAM_MAX_CLIENTS`): catalog is silent on capacity
   wiring; today's default 20 is hard-coded in `stream.py` - a knob is config surface beyond
   the closed catalog.
7. Eager debug f-strings at call sites (`http/server.py:64` access log per request,
   `engine/adapter.py:853,865`): formatted even when the level is filtered, but flagged OUT of
   the 4.2 file scope - needs its own before/after measurement to be kept (catalog rule).
8. Connect-phase hello-lag outlier follow-up: one client's registration->hello path lagged
   ~13 ms at connect (bimodal per run), upstream of all four wave-2.5 parts - residual tail
   risk, not tick cost; investigate the connect pipeline only if the tail reappears.
9. `duration_ms` index for `order=slowest`: the only remaining `USE TEMP B-TREE` plan;
   catalog item 13 names no such index.
10. `audit_log(action, ts)` index: filtered audit variant still scans on `action`; not in the
    catalog (its plan already improved to index scan + no sort for free).
11. Payload-covering indexes (all list columns inside each trace index): rejected against the
    catalog's own column list and the disk/write-amplification cost - revisit only if a
    measured list query misses budget.
12. `docs/architecture.md` section 5 index DDL sync (same seven `CREATE INDEX` lines as
    migration 0003 / schema.sql): mechanical doc drift, outside every group's file list.
13. Retention `LIMIT -1 OFFSET ?` selectors -> keyset: these are keep-newest-N delete
    predicates, not list pagination; converting changes tie-breaking for no measured win.
14. `metrics_summary` KPI strip reads `traces` for exact small-window numbers: rewriting would
    weaken a documented guarantee (api-reference section 6, anti-goal 3).
15. Per-cycle retention log line in server logs: catalog does not require it; counts already
    appear in `seed.py` output.
16. api-reference section 8 delivery wording ("as traces complete" -> "by the next tick"):
    behavior matches the intended tick-latency design; doc owner's wording touch-up later.
17. Deterministic integer wave windows (anchor + `floor(elapsed/tick)`): removes the sub-ms
    freshness-age/phase-drift race; current delivery measures under budget.
18. Span-less "model event" in the vocabulary: startup/prefetch loads log but emit no span, so
    D-014 `stats.load_ms`/`size_bytes` stay null for checkpoints that never load inside a
    traced request.
19. HF hub "Fetching 5 files" prints during cache verification: local cache inspection, no
    network bytes, pre-existing laya/hub behavior - cosmetic.

Backlog count: 19 entries (17 actionable, 1 resolved, 1 closed by decision).

## Not shipped or changed from the plan

- Catalog item 25 not applied (rejected, DEC-1). Everything else in the catalog shipped or
  was already compliant (21 applied + 9 verified no-change).
- No catalog change was reverted: all carry numbers (see keep table).
- Test-revert coupling: the three updated assertions in `tests/test_real_adapter.py` (group
  4.4: explicit-model `route.decide` absent, staged first-checkpoint sync, staged remainder
  queued) revert TOGETHER with the group - plan rollback couples group and tests. No other
  group touched a test file (`build_server()` is fixture code inside `http/server.py`).
- The full suite runs `365 passed + 1 failed`: the failure is the pre-existing environmental
  `test_legacy_state.py::test_real_repo_file_imports_zero_keys_and_renames` (repo-root
  `api_keys.json` fixture renamed by the live deployment); it belongs to the test-refit wave,
  not this phase.

## Acceptance criteria result

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | RSS <= 120 MB, recorded | PASS - 74.5 MB | `budget_check.py rss`, `budget-rss-20260923-014753.json` |
| 2 | Disk <= 200 MB at 10k traces, `du` recorded | PASS - 46.1 MB, `du` 47M | `budget-disk-20260923-014758.json` |
| 3 | Cold start <= 3 s to first served request | PASS - 355 ms | `budget-coldstart-20260923-014805.json` |
| 4 | Idle CPU < 2 % over 60 s | PASS - 0.13 % | `budget-idle-cpu-20260923-014820.json` |
| 5 | Overhead <= 3 ms p95, >= 200 requests, before/after in evidence | PASS - -0.378 ms, n=250/side | `20260923-014652.json`, overhead tables in evidence.md |
| 6 | Trace list <= 50 ms p95 incl. deep cursor | PASS - 4.094 worst, deep@5000 2.018 | same JSON, api table |
| 7 | Metrics <= 30 ms p95, rollup-served, no traces scan | PASS - 1.178 ms; plan rollup-only | same JSON + AFTER plan 9 |
| 8 | Static <= 5 ms p95, 304 when unchanged | PASS - 1.531 ms, 100/100 304 | same JSON, static table |
| 9 | SSE <= 5 ms/tick with 20 clients, no full-ring copy | PASS - 2.445 ms, 20/20 fed; index-snapshot rings (item 8) + coalesced tick (item 24) | same JSON, sse ladder |
| 10 | Baseline discipline: dated baseline + final delta + before/after per change | PASS | evidence.md baseline table (2026-09-22), FINAL DELTA TABLE, per-group tables |
| 11 | CI guard: overhead + api at 2x budget, committed JSON | PASS | `.github/workflows/ci.yml` `bench-guards` job (6 / 100 / 60 ms), JSONs under `bench/results/` |

## Budgets and metrics

| Metric | Target | Observed (final sweep 2026-09-23) |
|---|---|---|
| RSS overhead | <= 120 MB | 74.5 MB |
| Disk at 10k traces | <= 200 MB | 46.1 MB (`du` 47M) |
| Cold start | <= 3000 ms | 355 ms |
| Idle CPU | < 2 % / 60 s | 0.13 % |
| Recording overhead p95 | <= 3 ms | -0.378 ms |
| Trace list p95 (worst) | <= 50 ms | 4.094 ms (deep 2.018) |
| Metrics 15 m p95 | <= 30 ms | 1.178 ms |
| Static asset p95 | <= 5 ms + 304 | 1.531 ms, 100/100 304 |
| SSE tick p95, 20 clients | <= 5 ms | 2.445 ms (baseline 69.031 FAIL) |
| Throughput (no budget) | - | 888.6 req/s (baseline 773.2) |
| Bundle export | <= 3145728 B | 1403691 B (+712660 B sidecars) |

## CI regression guards (plan task 12)

- `.github/workflows/ci.yml` gains the `bench-guards` job: seeds 10k traces, runs
  `scripts/bench.py overhead` and `scripts/bench.py api`, then reads the newest
  `bench/results/*.json` and FAILS when any row exceeds 2x its section 1 budget:
  **overhead 3 -> 6 ms p95, traces 50 -> 100 ms p95, metrics 30 -> 60 ms p95** (thresholds
  stated as env vars in the yaml, derived from `docs/performance.md` section 1/section 6).
  Each run writes its JSON under `bench/results/` inside the job, so drift stays visible.
- `scripts/budget_check.py` at the HARD thresholds (120 MB / 200 MB / 3 s / 2 %) is the
  **Phase 8 release gate**, run once on the packaged image on the release machine - CI does
  not run it (noisy shared machines are tolerated at 2x only).

## What the next phase inherits

- `evidence.md`: dated baseline table, per-group before/after tables, nine BEFORE + nine
  AFTER `EXPLAIN` plans, tracemalloc/strace captures, final delta table with fresh numbers.
- Committed harness JSONs under `bench/results/` (baseline, per-group, wave-2.5 ladder,
  final sweep) for drift comparison.
- Re-runnable recipe: `scripts/seed.py --traces 10000 --observations 9` + `scripts/bench.py
  all` + four `budget_check.py` subcommands (engineless children, throwaway state dir).
- CI guards at 2x budget; hard gate scripts for Phase 8.
- GREEN-29 (`test_stream` + `test_wire` + `test_middleware`) as the SSE/wire contract;
  53 recorder-scoped tests; 136 storage-scoped tests.
- The 19-entry backlog above.

## Follow-ups

- Phase 8: run `scripts/budget_check.py rss|disk|coldstart|idle-cpu` at hard thresholds on the
  packaged image (release gate).
- Test-refit wave: pre-existing `test_legacy_state` repo-root fixture failure.
- Backlog items 1-19 above (start with 6 capacity knob, 7 eager f-strings, 8 hello-lag tail).
