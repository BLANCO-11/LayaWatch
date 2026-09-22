# Phase 7 - Performance and optimization

Status: planned
Depends on: phase 6
Estimated effort: 4 to 6 days
Deliverable: every budget in `docs/performance.md` section 1 is met on the final Phase 6 code, with
baseline and after numbers in evidence, catalog changes kept or reverted by measurement, and CI
regression guards running the harnesses.

## Objective

Prove and improve measured performance under the rules of D-009 and D-010: capture a baseline of all
nine budgets against the seeded database first, then apply the closed optimization catalog of
`docs/performance.md` section 4 in its documented order, keeping only changes with measured wins or
clear robustness gains and recording before/after numbers for every recorder, writer and query
change.

## Scope

In: harness completion (`scripts/bench.py`, `scripts/seed.py`, `scripts/budget_check.py`), baseline
capture, catalog groups 4.3 storage and 4.2 recorder, then 4.1 request path and 4.4 engine
interaction, then 4.5 streaming and 4.6 frontend, `EXPLAIN QUERY PLAN` captures for every list query,
before/after tables in `plan/phase-7-optimization/evidence.md`, keep/revert outcome plus a backlog
section in `plan/phase-7-optimization/outcome.md`, CI regression guards at 2x budget in
`.github/workflows/ci.yml`, committed JSON under `bench/results/`.

Out: the hard release gate itself (runs at Phase 8 with `budget_check.py` on the packaged image),
packaging and image-size work (Phase 8), any new optimization outside the closed catalog (backlog
only, per R-11), C extensions and caching layers that can serve stale correctness-critical data
(anti-goals, `docs/performance.md` section 3), engine forward-pass optimization beyond avoiding work
(`docs/performance.md` section 1 closing note).

## Deliverables

```
scripts/bench.py               overhead, throughput, api, static, sse, all; table output plus JSON to bench/results/<timestamp>.json
scripts/seed.py                --traces 10000 --observations 9 seeding, idempotent, realistic route/status/model mix
scripts/budget_check.py        rss, disk, coldstart, idle-cpu subcommands completed and hardened (started in Phase 1)
bench/results/                 committed JSON per harness run: baseline, per-group, final
.github/workflows/ci.yml       added jobs: bench.py overhead and bench.py api at 2x budget thresholds
plan/phase-7-optimization/evidence.md   baseline table, before/after tables per group, EXPLAIN QUERY PLAN captures, final delta table
plan/phase-7-optimization/outcome.md    kept vs reverted list with numbers, plus backlog section for deferred ideas
tests/                         regression tests for kept changes (harness contracts, SSE registry, prepared-statement reuse)
```

## Tasks

1. Baseline first: finish `scripts/seed.py` so `scripts/seed.py --traces 10000 --observations 9`
   builds the seeded database, then run every harness in `docs/performance.md` section 2
   (`scripts/bench.py all` covering overhead, throughput, api, static, sse, and
   `scripts/budget_check.py rss`, `disk`, `coldstart`, `idle-cpu`) and record every number in
   `plan/phase-7-optimization/evidence.md`. No catalog change may start before this baseline exists.
2. Complete the harnesses: `scripts/budget_check.py` asserts the four resource budgets and exits
   non-zero on failure; `scripts/bench.py` subcommands each print a table (p50, p90, p95, p99, max,
   n) and write JSON to `bench/results/<timestamp>.json`; both read the same seeded database and
   document their invocation in `--help`.
3. Storage group (`docs/performance.md` 4.3, items 11 to 18): apply the SQLite pragmas, batched
   `executemany` inserts, covering indexes for the list queries of `docs/api-reference.md`,
   keyset-only pagination, writer-tick rollups, chunked retention deletes, idle
   `wal_checkpoint(TRUNCATE)` with the 20 % `VACUUM` guard, and `PRAGMA optimize`. Capture
   `EXPLAIN QUERY PLAN` for each list query before and after. Re-run `scripts/bench.py api` and
   `scripts/budget_check.py disk`.
4. Recorder group (4.2, items 6 to 10): `__slots__` on `Trace` and `Span`, two `perf_counter` calls
   per span with no `datetime`, fixed-size `deque` rings with index-snapshot reads for SSE, skipping
   log-line formatting when the level is filtered, and the sampling decision once per trace before
   span assembly. Re-run `scripts/bench.py overhead` plus a `tracemalloc` snapshot; record
   before/after numbers in evidence.
5. Request path group (4.1, items 1 to 5): keep-alive plus `TCP_NODELAY`, single `bytes`
   serialization of responses, one-shot `read(length)` body handling with oversized rejection,
   per-thread prepared statement cache, cached gzip decision with a 1 KB floor. Re-run
   `scripts/bench.py overhead`, `api` and `static`.
6. Engine interaction group (4.4, items 19 to 23): `torch.inference_mode()` around every forward
   pass, `torch.set_num_threads(min(cores, 8))`, one warmup pass per checkpoint at startup, skipping
   `lang.detect` when the client pins `lang=` and `route.decide` when `model=` is explicit,
   background prefetch only when `LAYA_MODELS` lists the checkpoint and idle CPU allows, and
   tokenizer/config reuse per checkpoint. Engine correctness must not change: the full test suite and
   `scripts/smoke_laya.py` must produce identical predictions for pinned inputs, and spans absent by
   design (skipped `lang.detect` or `route.decide`) must be documented as such in
   `docs/observability-model.md` in the same change. Re-run `scripts/bench.py throughput` and the
   cold-start evidence.
7. Streaming group (4.5, items 24 to 26): one coalesced payload per SSE tick with diffs only,
   heartbeat only after 15 s of socket idle, and dead-client drop on write error. Re-run
   `scripts/bench.py sse` with 20 clients.
8. Frontend group (4.6, items 27 to 31): confirm static export with no chart library, route-level
   code splitting, SSE in place of polling with polling as fallback only, skeletons plus
   `content-visibility: auto` plus log virtualization beyond 500 lines, and precompressed `.br`/`.gz`
   assets served directly. Record the bundle size report; re-run `scripts/bench.py static` and
   `sse`.
9. Query plan evidence: for every list query in `docs/api-reference.md` (traces newest, traces by
   status, traces by route, traces by model, logs tail, observations by trace, audit entries) capture
   `EXPLAIN QUERY PLAN` output on the seeded 10k database before the storage group and after, and
   paste both plans into `plan/phase-7-optimization/evidence.md`.
10. Final measurement: re-run every harness (`scripts/bench.py all`, all four
    `scripts/budget_check.py` subcommands) and write the delta table against the baseline into
    evidence.md: budget, baseline, final, delta, verdict.
11. Keep or revert, per catalog rule: walk every applied item and keep it only with a measured win or
    a clear robustness gain; revert the rest and say so with numbers in
    `plan/phase-7-optimization/outcome.md`. Every recorder, writer or query change carries explicit
    before/after numbers in evidence.md; anything unmeasured is reverted (D-009, R-10).
12. Regression guards: update `.github/workflows/ci.yml` to run `scripts/bench.py overhead` and
    `scripts/bench.py api` on every change with 2x budget thresholds, and commit the JSON results
    under `bench/results/` so drift is visible in review; record in outcome.md that
    `scripts/budget_check.py` at hard thresholds is the Phase 8 release gate.
13. Backlog: collect every optimization idea outside or beyond the closed catalog (including items
    proposed during the phase and reverted for lack of measurement) into a backlog section of
    `plan/phase-7-optimization/outcome.md` with a one-line rationale each, per R-11.

## Acceptance criteria

1. RSS budget: `scripts/budget_check.py rss` exits 0 against the warmed-up seeded deployment with
   LayaWatch overhead (excluding model weights) <= 120 MB, number recorded in evidence.md.
2. Disk budget: `scripts/budget_check.py disk` exits 0 with the state directory at <= 200 MB after
   seeding 10k traces and one retention run, `du -sh` output recorded.
3. Cold-start budget: `scripts/budget_check.py coldstart` exits 0 with time to first served request
   <= 3 s excluding model load, measured from startup log timestamps.
4. Idle-CPU budget: `scripts/budget_check.py idle-cpu` exits 0 with the 60 s average below 2 % with
   the UI closed and no clients connected.
5. Overhead budget: `scripts/bench.py overhead` reports recording overhead <= 3 ms p95 added on
   engine latency over at least 200 requests, with the before/after table in evidence.md.
6. Trace list budget: `scripts/bench.py api` reports `GET /api/v1/traces` at <= 50 ms p95 on the
   10k-trace seeded database, including a cursor deep in the table.
7. Metrics budget: `scripts/bench.py api` reports `GET /api/v1/metrics?range=15m` at <= 30 ms p95,
   served from rollups with no `traces` scan in its query plan.
8. Static budget: `scripts/bench.py static` reports static asset responses at <= 5 ms p95 and a
   conditional re-request of an unchanged asset returns `304`.
9. SSE budget: `scripts/bench.py sse` reports <= 5 ms per tick with 20 connected clients, and the
   tick does not copy the full ring.
10. Baseline discipline: evidence.md contains the complete baseline table for all nine budgets of
    `docs/performance.md` section 1, dated before the first optimization commit, plus the final
    delta table; every recorder, writer and query change in the outcome has before/after numbers.
11. CI guard: `.github/workflows/ci.yml` runs `scripts/bench.py overhead` and
    `scripts/bench.py api` and fails when either exceeds 2x its section 1 budget, with committed
    JSON under `bench/results/`.

## Evidence required

- `scripts/seed.py --traces 10000 --observations 9` output (row counts, database size).
- Baseline capture: `scripts/bench.py all` and `scripts/budget_check.py rss|disk|coldstart|idle-cpu`
  output pasted into `plan/phase-7-optimization/evidence.md`, plus the JSON files under
  `bench/results/`.
- Per-group re-runs: `bench.py api` after 4.3, `bench.py overhead` after 4.2 and 4.1,
  `bench.py throughput` plus cold-start log after 4.4, `bench.py sse` after 4.5, `bench.py static`
  and bundle size report after 4.6, each as before/after tables in evidence.md.
- `EXPLAIN QUERY PLAN` captures for every list query before and after the index work, showing
  index-only scans for list views.
- `tracemalloc` snapshot comparison for the recorder changes; `strace -c` comparison for the
  tokenizer reuse item (4.4 item 23).
- Final `scripts/bench.py all` plus all four `budget_check.py` subcommands, and the delta table
  (budget, baseline, final, delta, verdict).
- Keep/revert list with numbers and the backlog section in
  `plan/phase-7-optimization/outcome.md`.

## Risks and mitigations

|Risk|Mitigation|
|---|---|
| An optimization regresses correctness or durability (R-10) | full `make test` plus `scripts/smoke_laya.py` after each catalog group; before/after numbers required for every recorder, writer and query change; revert anything unmeasured (`docs/performance.md` section 6)|
| Noisy shared machine makes CI benches flaky | CI thresholds at 2x budget only; the hard `budget_check.py` gate runs once at Phase 8 on the release machine; machine and load recorded in the evidence environment block|
| Baseline captured under abnormal load makes all deltas misleading | baseline includes environment notes; if the machine changed, the whole baseline is re-captured before any change, still ahead of the first edit|
| Scope creep beyond the closed catalog (R-11) | catalog is binding; new ideas go only to the backlog section of outcome.md, never into the phase|
| Engine behavior drift from 4.4 items (inference_mode, thread count, warmup, skipped detects) | pinned-input prediction comparison, unchanged test suite, spans absent by design documented in `docs/observability-model.md`; prefetch gated on `LAYA_MODELS` plus idle CPU and the RAM guard|
| A caching shortcut sneaks in and serves stale data | anti-goal in `docs/performance.md` section 3 is enforced in review: no in-memory duplicate of traces; prepared statements and gzip decisions are not data caches|
| Seeded database shape differs from production, so wins do not transfer | `scripts/seed.py` mirrors the real route/status/model mix and retention state; the same seed is used for baseline, per-group and final runs|

## Rollback

Each catalog group lands as its own commit, so a group that fails its re-run or its acceptance
criterion is reverted by reverting that commit; the harness scripts and the baseline in evidence.md
are additive and survive any revert. If a kept change fails a budget at the Phase 8 gate, it is
reverted to the last `bench/results/` JSON that met the budget, and evidence.md records the revert
with the restoring numbers. The engine group additionally depends on `scripts/smoke_laya.py`
producing identical predictions, so an engine regression blocks the group before it merges.

## Exit gate

All 11 acceptance criteria verified with observed commands, baseline and delta tables in
`plan/phase-7-optimization/evidence.md`, keep/revert outcome plus backlog in
`plan/phase-7-optimization/outcome.md`, decisions recorded for every kept change, and the CI guards
green at 2x budget. The Phase 8 release gate (hard-threshold `scripts/budget_check.py` on the
packaged image) depends on these numbers: without the committed baseline, per-group before/after
tables and final deltas, Phase 8 cannot gate the release.
