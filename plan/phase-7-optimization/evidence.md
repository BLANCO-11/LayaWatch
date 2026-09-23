# Phase 7 - Performance and optimization: evidence

Status: BASELINE CAPTURED 2026-09-22, before any optimization-catalog change (plan task 1, task
2, task 9-before). No `layawatch/` code was modified for this capture.
Status (update 2026-09-23, wave 3): FINAL SWEEP DONE (plan task 10) - all nine budgets PASS.
Per-group before/after tables, the AFTER `EXPLAIN` plans, the tracemalloc/strace captures and the
FINAL DELTA TABLE follow the baseline sections below. The baseline table itself is untouched.
Rule: commands plus observed output. "Looks fine" is not evidence.

## Environment

- Commit: `3aaaa2beaa4cb83929e00c82135eccefdbdc6df7` (read from `.git/refs/heads/main`; no git
  commands run, per assignment).
- Machine: `ML-AZ-CI-AICOE-VM-Foundry-1`, Linux `6.8.0-1064-azure`, AMD EPYC 7763 64-Core
  Processor, **4 CPUs available to the process** (`nproc` = sched affinity = 4, host chip has 64),
  MemTotal 16 GB.
- Load during capture: loadavg `1.06 1.04 0.99` at `bench.py all` start (recorded inside
  `bench/results/20260922-231552.json`), `1.05 0.94 0.95` after the budget checks - quiet box.
- Versions: Python 3.12.14 (`.venv`), SQLite 3.53.1, uvicorn 0.53.0, FastAPI 0.141.1,
  Node v24.19.0, ruff 0.16.8 (config: line-length 100, target py312).
- Topology: one throwaway seeded state dir `/tmp/lw-bench`; `bench.py` children on
  `LW_BENCH_PORT=8091`; `budget_check.py` children on `LAYA_PORT=8099`; every child boots
  engineless (launch nulls `sys.modules["laya"]` before `layawatch/__main__.py`'s
  `find_spec("laya")` probe, so `FakeAdapter` serves, healthz `device=fake`, no checkpoint can
  load - P1-I3 parallel-load warning respected). The live box on `:8050` was never touched and
  was the only listener on 8050/8091/8099 after the capture (throwaways all stopped).

## Seeding (plan task 1 evidence)

```
$ rm -rf /tmp/lw-bench
$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/seed.py --traces 10000 --observations 9
seed: state /tmp/lw-bench (db state.sqlite3)
seed: inserting 11000 traces over 5.0d + rollups + logs + audit, then one retention pass
seed: inserted traces=11000 observations=99000 rollups(+errors)=129603+19852 logs=2500 audit=400
seed: retention pass {"logs_pruned": 500, "payloads_nulled": 0, "pending_parents_built": 2478,
      "pending_rollups_built": 2282, "rollups_pruned": 0, "sessions_pruned": 0,
      "traces_pruned": 1000, "vacuumed": 0, "wal_checkpointed": 1}
seed: rows after retention {"api_keys": 0, "audit_log": 400, "log_entry": 2000,
      "metric_rollup": 190817, "observations": 90000, "scores": 0, "sessions": 0,
      "traces": 10000, "users": 0}
seed: db size state.sqlite3=46972928 state.sqlite3-wal=0 state.sqlite3-shm=65536
      total=47038464 bytes (44.86 MB)
seed: marker /tmp/lw-bench/.lw-seed.json (22.5s)
exit=0
```

- Second run (idempotency): identical budget-table counts - traces 10000, observations 90000,
  log_entry 2000, audit_log 400, users/sessions/api_keys 0; identical 44.86 MB file bytes.
  `metric_rollup` reproduced at 190840 (+23 rows, 0.01 %): the helpers derive 10 s bucket
  boundaries from wall-clock time, so a re-run minutes later lands traces against slightly
  different buckets. The marker-guard refuses any directory with traces but no
  `.lw-seed.json` (the live box can never be seeded).
- One retention run is part of the seed command (counts above: 1000 pruned to the
  `retention_traces=10000` cap, 500 logs pruned to `log_ring_size=2000`, 2282 pending rollups +
  2481... see exact numbers above, WAL checkpoint truncated to 0 bytes).
- After the harness runs (bench engine traffic churns the cap):
  `du -sh /tmp/lw-bench` = **45M**; rows: traces **10000** (9095 seeded + 905 bench `/predict`
  traces), observations **85475** (= 9095x9 + 905x4; engine traces carry only the middleware's
  four spans under `FakeAdapter`), log_entry 2000, metric_rollup 195078 (writer added live
  buckets during bench), audit_log 400.

## Baseline table - all nine budgets, docs/performance.md section 1 (2026-09-22)

| Budget | Target | Baseline observed (2026-09-22) | Verdict at baseline |
|---|---|---|---|
| LayaWatch RSS overhead (no model weights) | <= 120 MB | **50.0 MB** (`budget_check.py rss`) | PASS |
| LayaWatch disk at 10k traces | <= 200 MB | **44.8 MB** state dir; `du -sh` = **45M** (`budget_check.py disk`) | PASS |
| Cold start to first served request | <= 3000 ms | **360 ms** (`budget_check.py coldstart`, launch -> first 200 on /healthz) | PASS |
| Idle CPU with no clients | < 2 % over 60 s | **0.13 %** (`budget_check.py idle-cpu`, 60 s window, UI closed, no clients) | PASS |
| Recording overhead on engine latency | <= 3 ms p95 added | **-0.001 ms** p95 added; record_on p95 1.878 ms vs record_off p95 1.879 ms, n=250 per side (`bench.py overhead`, `LAYWATCH_RECORD=1` vs `0`, fake engine) | PASS |
| Trace list query, 10k traces | <= 50 ms p95 | shallow **5.729 ms** p95; deep cursor @offset 5000 **5.277 ms** p95; worst **5.729 ms** (`bench.py api`, n=100 each) | PASS |
| Metrics query, 15 m window | <= 30 ms p95 | **2.309 ms** p95 (`bench.py api`, `metrics=requests,latency,errors,queue&range=15m`, n=100; query plan reads only `metric_rollup` - see BEFORE plans) | PASS |
| Static asset response | <= 5 ms p95, `304` when unchanged | **2.751 ms** p95; conditional re-requests **100/100 returned 304** (`bench.py static`, n=100) | PASS |
| SSE tick cost with 20 clients | <= 5 ms per tick | **69.031 ms** p95 (p50 32.009, max 69.031, n=12 waves) measured across the **8 of 20 clients that received stream data**; 16 registered, 8 starved post-200, 4 rejected `503 sse_unavailable` (`bench.py sse`) | **FAIL** - recorded, not fixed (baseline rule). 20-client precondition unmeetable on stock code: see Gaps |

Baseline is a measurement, not a gate: the SSE row is recorded as-is; no code was changed.

## Raw harness outputs

```
$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py all
bench overhead (milliseconds)
series                         p50       p90       p95       p99       max       n
record_on                    1.161     1.521     1.878     2.525    15.373     250
record_off                   1.254     1.648     1.879     3.357    14.591     250
overhead_added              -0.093    -0.127    -0.001    -0.832     0.782     250
budget overhead: -0.001 ms (limit 3.0 ms) -> PASS
throughput: 773.2 req/s (300 requests in 0.39 s, fake engine)

bench throughput (milliseconds)
series                         p50       p90       p95       p99       max       n
predict_latency               1.145     1.379     1.555     2.346    20.338     300

bench api (milliseconds)
series                         p50       p90       p95       p99       max       n
traces                        2.520     5.402     5.729     9.220    17.570     100
traces_deep                   3.532     3.851     5.277     5.485     5.513     100
metrics_15m                   1.866     2.146     2.309     2.562     2.586     100
budget traces: 5.729 ms (limit 50.0 ms) -> PASS  [worst of shallow 5.729 / deep 5.277 @5000]
budget metrics_15m: 2.309 ms (limit 30.0 ms) -> PASS

bench static (milliseconds)
series                         p50       p90       p95       p99       max       n
static_asset                  1.497     2.340     2.751     3.691     3.750     100
static_conditional_304        1.257     1.427     1.481     1.582     3.601     100
budget static: 2.751 ms (limit 5.0 ms) -> PASS  [/_next/static/chunks/0-fgc7lu98d9c.js;
                     conditional re-requests: 100/100 returned 304]

bench sse (milliseconds)
series                         p50       p90       p95       p99       max       n
sse_tick                     32.009    67.225    69.031    69.031    69.031      12
budget sse_tick: 69.031 ms (limit 5.0 ms) -> FAIL  [only 8/20 clients received stream data;
        8 of 16 registered starved after HTTP 200 (8-worker event-loop default executor);
        4 rejected ['sse_unavailable'] at hub max_clients=16]
bench: wrote bench/results/20260922-231552.json
exit=0

$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py rss
name             value    budget  result
rss            50.0 MB    120 MB  PASS        (wrote bench/results/budget-rss-20260922-231713.json)
exit=0
$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py disk
disk           44.8 MB    200 MB  PASS        (wrote bench/results/budget-disk-20260922-231704.json)
exit=0
$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py coldstart
coldstart       360 ms   3000 ms  PASS        (wrote bench/results/budget-coldstart-20260922-231715.json)
exit=0
$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py idle-cpu
idle-cpu        0.13 %       2 %  PASS        (wrote bench/results/budget-idle-cpu-20260922-231723.json)
exit=0
```

Throughput has no section 1 budget; recorded for the 4.4 engine group's before/after.

## Artifacts

- bench JSON: `bench/results/20260922-231552.json` (subcommand `all`: series, budgets, scalars,
  environment block).
- budget JSON: `bench/results/budget-{rss,disk,coldstart,idle-cpu}-20260922-*.json` (4 files).
- seeded state: `/tmp/lw-bench/state.sqlite3` (throwaway, 45M after capture; re-seedable).
- screenshots: not applicable (harness capture; no UI surface changed).

## Gaps

1. **SSE tick budget FAILS at baseline, recorded not fixed**: two independent ceilings block the
   20-client precondition on stock code - (a) the ASGI bridge runs each stream's producer as a
   lifetime-bound thread on the event-loop default executor (`min(32, cpu+4)` = **8 workers** on
   this 4-cpu box; verified `os.cpu_count()==4` and executor `max_workers==8`, and exactly the
   first 8 registered clients received bytes while 8 got HTTP 200 then silence), and (b) the hub
   rejects past `max_clients=16` (`4 x 503 sse_unavailable`). Even the 8 data-fed clients
   measure **p50 32.009 ms / p95 69.031 ms** per wave (max-min arrival, hello-anchored) versus
   the 5 ms budget. Remediation would touch `layawatch/` (stream pump executor, capacity
   config) - out of this slice; owner should route it to the streaming/catalog group or an
   issue before task 7 re-runs `bench.py sse`.
2. Post-bench observation count is 85475, not the seed-time 90000: bench wrote 905 engine
   traces which evicted seed rows at the `retention_traces=10000` cap (reconciled exactly:
   9095x9 + 905x4 = 85475). `bench.py api` records `traces_at_start`/`traces_at_end` in its
   JSON scalars.
3. `metric_rollup` row count is wall-clock sensitive (+/-0.02 % between identical seeds) and
   grows with writer activity during bench (190840 -> 195078). Budgeted tables reproduce
   exactly; rollup VALUES are synthetic per `tests/seed.py` (4 requests per 10 s bucket) -
   shapes and plans are what the budgets measure.
4. `budget_check.py all` still stops at the first breach by design; the baseline ran each
   subcommand separately so every number exists regardless of verdicts.
5. The EXPLAIN capture script lives at `/tmp/lw_explain.py` (throwaway, outside the repo); the
   SQL and plans below are the durable record.

## BEFORE plans (plan task 9, captured on the seeded 10k database before any storage change)

Captured by running the real `layawatch.store.queries` functions through a recording
connection proxy (exact SQL as issued) plus `EXPLAIN QUERY PLAN` on
`/tmp/lw-bench/state.sqlite3`; deep cursor reached offset 5000 after 100 keyset pages.

### 1. traces newest - `GET /api/v1/traces?limit=50`

```sql
SELECT id, ts_start, duration_ms, route, method, status, model, route_reason, queue_ms,
       forward_ms, client_key_id, error_code FROM traces
WHERE 1 ORDER BY ts_start DESC, id DESC LIMIT ?   -- [51]
```
```
SCAN traces USING INDEX traces_ts
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
```

Page span counts (second statement of the same request):

```sql
SELECT trace_id, COUNT(*) FROM observations WHERE trace_id IN (<50 page ids>)
GROUP BY trace_id
```
```
SEARCH observations USING COVERING INDEX observations_trace (trace_id=?)
```

Total estimate (third statement; `WHERE 1` is the no-filter case):

```sql
SELECT COUNT(*) FROM traces WHERE 1   -- []
```
```
SCAN traces USING COVERING INDEX traces_ts
```

### 2. traces by status - `GET /api/v1/traces?status=200&limit=50`

```sql
... FROM traces WHERE status = ? ORDER BY ts_start DESC, id DESC LIMIT ?   -- [200, 51]
```
```
SEARCH traces USING INDEX traces_status_ts (status=?)
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
```
```sql
SELECT COUNT(*) FROM traces WHERE status = ?   -- [200]
```
```
SEARCH traces USING COVERING INDEX traces_status_ts (status=?)
```

### 3. traces by route - `GET /api/v1/traces?route=/predict&limit=50`

```sql
... FROM traces WHERE route = ? ORDER BY ts_start DESC, id DESC LIMIT ?   -- ['/predict', 51]
```
```
SEARCH traces USING INDEX traces_route_ts (route=?)
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
```
```sql
SELECT COUNT(*) FROM traces WHERE route = ?   -- ['/predict']
```
```
SEARCH traces USING COVERING INDEX traces_route_ts (route=?)
```

### 4. traces by model - `GET /api/v1/traces?model=english&limit=50`

```sql
... FROM traces WHERE model = ? ORDER BY ts_start DESC, id DESC LIMIT ?   -- ['english', 51]
```
```
SEARCH traces USING INDEX traces_model_ts (model=?)
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
```
```sql
SELECT COUNT(*) FROM traces WHERE model = ?   -- ['english']
```
```
SEARCH traces USING COVERING INDEX traces_model_ts (model=?)
```

### 5. traces deep keyset page - `GET /api/v1/traces?limit=50&cursor=<offset 5000>`

```sql
... FROM traces
WHERE (ts_start < ? OR (ts_start = ? AND id < ?))
ORDER BY ts_start DESC, id DESC LIMIT ?
-- [1789922048.0027244, 1789922048.0027244, '00001770', 51]
```
```
MULTI-INDEX OR
INDEX 1
SEARCH traces USING INDEX traces_ts (ts_start<?)
INDEX 2
SEARCH traces USING INDEX traces_ts (ts_start=?)
USE TEMP B-TREE FOR ORDER BY
```
(The deep page's `total_estimate` statement is the unfiltered `COUNT(*) ... WHERE 1` of
capture 1.)

### 6. logs tail - `GET /api/v1/logs?limit=50`

```sql
SELECT id, ts, level, source, trace_id, message FROM log_entry
WHERE 1 ORDER BY ts DESC, id DESC LIMIT ?   -- [51]
```
```
SCAN log_entry USING INDEX log_ts
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
```
```sql
SELECT COUNT(*) FROM log_entry WHERE 1   -- []
```
```
SCAN log_entry USING COVERING INDEX log_ts
```

### 7. observations by trace - `GET /api/v1/traces/00002af7/observations`

```sql
SELECT * FROM observations WHERE trace_id = ? ORDER BY start_ms, id   -- ['00002af7']
```
```
SEARCH observations USING INDEX observations_trace (trace_id=?)
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
```

### 8. audit entries - `GET /api/v1/audit?limit=50`

```sql
SELECT id, ts, actor_id, actor, action, target, result, meta FROM audit_log
WHERE 1 ORDER BY ts DESC, id DESC LIMIT ?   -- [51]
```
```
SCAN audit_log USING INDEX audit_ts
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
```
```sql
SELECT COUNT(*) FROM audit_log WHERE 1   -- []
```
```
SCAN audit_log USING COVERING INDEX audit_ts
```

Filtered variant `GET /api/v1/audit?action=key.created&limit=50` (no index on `action`):

```sql
... WHERE action = ? ORDER BY ts DESC, id DESC LIMIT ?   -- ['key.created', 51]
```
```
SCAN audit_log
USE TEMP B-TREE FOR ORDER BY
```
```sql
SELECT COUNT(*) FROM audit_log WHERE action = ?   -- ['key.created']
```
```
SCAN audit_log
```

### 9. metrics 15 m - `GET /api/v1/metrics?metrics=requests,latency,errors,queue&range=15m`

```sql
SELECT bucket, metric, count, sum, min, max, p50, p90, p95, p99 FROM metric_rollup
WHERE step = ? AND metric IN (?,?,?,?) AND bucket >= ? AND bucket < ?
-- [10, 'requests', 'latency', 'errors', 'queue', 1790117590, 1790118492.6115541]
```
```
SEARCH metric_rollup USING INDEX rollup_metric_bucket
  (metric=? AND step=? AND bucket>? AND bucket<?)
```

Already rollup-only **before** the storage group: no `traces` scan in the plan, satisfying the
"no traces scan" half of acceptance criterion 7 at baseline; the work remaining for the AFTER
capture is the sort-term/`USE TEMP B-TREE` entries above (catalog 4.3 items 13/14/18).

---

# Merged group evidence (wave 3 fold, plan tasks 3-8 + 9-AFTER + 10)

The six per-group fragments `evidence-4.{1,2,3,4,5,6}.md` stay in this directory as the
per-group provenance records (full verification trails, probes, revert-coupling notes); their
numbers, AFTER plans and captures are folded in below so this file alone carries the complete
record the plan deliverable requires.

## Final-sweep environment (plan task 10, wave 3, 2026-09-23)

- Same box as the baseline: host `ML-AZ-CI-AICOE-VM-Foundry-1`, Linux `6.8.0-1064-azure`, AMD
  EPYC 7763, `nproc`=4, MemTotal 16 GB; Python 3.12.14 (`.venv`), SQLite 3.53.1, uvicorn 0.53.0,
  FastAPI 0.141.1, ruff 0.16.8 (line-length 100, py312).
- Code state entering the sweep: wave 1 (groups 4.3 + 4.5 + 4.6), wave 2 (4.2 + 4.1) and
  wave 2.5 (SSE program parts 1-3, instrumentation removed) all landed. The sweep changed no
  code; every number below is fresh.
- Same seeded throwaway `/tmp/lw-bench` (47M on disk after all group churn; `budget_check.py
  disk` reports 46.1 MB). Bench children boot engineless on `LW_BENCH_PORT=8091`; budget children
  on `LAYA_PORT=8099`. The live `:8050` hub was never touched; after the sweep `ss -ltn` shows
  8091/8099 free (no leftover instance of mine).
- Load, each step sequential and standalone (loadavg printed before each): bench start
  `0.66 0.71 0.69` (the JSON env block inside the run records `0.68 0.73 0.70`); rss
  `0.74 0.74 0.70`; disk `0.68 0.73 0.70`; coldstart `0.62 0.72 0.70`; idle-cpu start
  `0.55 0.70 0.69`, end `0.33 0.61 0.66`. Quiet box throughout.

### Commands + observed outputs (the final measurement, run one at a time)

```
$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py all

bench overhead (milliseconds)
series                         p50       p90       p95       p99       max       n
record_on                    0.950     1.182     1.261     2.671    13.266     250
record_off                   1.054     1.377     1.639     4.234    13.181     250
overhead_added              -0.104    -0.195    -0.378    -1.563     0.085     250
budget overhead: -0.378 ms (limit 3.0 ms) -> PASS  [p95 of (record_on - record_off), n=250
                     per side, POST /predict, fake engine, LAYWATCH_RECORD=1 vs 0]
throughput: 888.6 req/s (300 requests in 0.34 s, fake engine)

bench throughput (milliseconds)
series                         p50       p90       p95       p99       max       n
predict_latency              0.974     1.345     1.588     2.455    17.791     300

bench api (milliseconds)
series                         p50       p90       p95       p99       max       n
traces                        1.589     3.750     4.094     6.568    25.513     100
traces_deep                   1.644     1.804     2.018     2.230     2.334     100
metrics_15m                   1.075     1.159     1.178     1.248     1.252     100
budget traces: 4.094 ms (limit 50.0 ms) -> PASS  [worst of shallow p95 4.094 ms and deep-cursor
              p95 2.018 ms at offset 5000 (100 keyset pages walked)]
budget metrics_15m: 1.178 ms (limit 30.0 ms) -> PASS
              [GET /api/v1/metrics?metrics=requests,latency,errors,queue&range=15m]

bench static (milliseconds)
series                         p50       p90       p95       p99       max       n
static_asset                  1.132     1.453     1.531     1.583     1.966     100
static_conditional_304        0.976     1.080     1.128     1.253     1.554     100
budget static: 1.531 ms (limit 5.0 ms) -> PASS  [/_next/static/chunks/0-fgc7lu98d9c.js;
                     conditional re-requests: 100/100 returned 304]

bench sse (milliseconds)
series                         p50       p90       p95       p99       max       n
sse_tick                     2.024     2.255     2.445     2.445     2.445      13
budget sse_tick: 2.445 ms (limit 5.0 ms) -> PASS  [per wave: max-min arrival delta across
        data-fed clients, each clock anchored on its own hello; first 2 waves skipped;
        scalars: clients_requested=20, registered=20, data_fed=20, starved=0, rejected=0]
bench: wrote bench/results/20260923-014652.json
exit=0
(real 0m54.4s)

$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py rss
name             value    budget  result
rss            74.5 MB    120 MB  PASS
budget_check: wrote bench/results/budget-rss-20260923-014753.json
exit=0

$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py disk
disk           46.1 MB    200 MB  PASS
budget_check: wrote bench/results/budget-disk-20260923-014758.json
exit=0
$ du -sh /tmp/lw-bench -> 47M

$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py coldstart
coldstart       355 ms   3000 ms  PASS
budget_check: wrote bench/results/budget-coldstart-20260923-014805.json
exit=0

$ LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py idle-cpu
idle-cpu        0.13 %       2 %  PASS
budget_check: wrote bench/results/budget-idle-cpu-20260923-014820.json
exit=0
```

All nine budgets PASS at the final sweep; no budget missed, nothing was fixed after the sweep.

## Per-group before/after tables (plan tasks 3-8; numbers from the group fragments and the
committed `bench/results/*.json`)

Column "after wave 1" is the single post-wave-1 run `20260923-001757.json` (groups 4.3 + 4.5 +
4.6 had all landed; its budget siblings are `budget-*-20260923-001856/001858.json`). Wave-2
numbers come from `20260923-003544/003606/003610` (4.2) and `20260923-005810/005815/005817/
005854/005856` (4.1). Wave-2.5 numbers from `20260923-012115..014028`. Final from
`20260923-014652` + the four `budget-*-20260923-0147/0148*.json`.

### Group 4.3 storage (wave 1, items 11-18) - fragment `evidence-4.3.md`

| Metric | Baseline (2026-09-22) | After wave 1 (20260923-001757) | Delta | Final (20260923-014652) |
|---|---|---|---|---|
| traces list p95, worst-of (budget 50 ms) | 5.729 ms (shallow 5.729 / deep@5000 5.277) | 2.847 / deep 2.859 -> worst 2.859 | worst -2.870 ms (-49.9 %); deep -2.418 (-45.6 %) | 4.094 / deep 2.018 -> worst 4.094 |
| metrics_15m p95 (budget 30 ms) | 2.309 ms | 1.771 ms | -0.538 ms (-23.3 %) | 1.178 ms |
| overhead_added p95 (budget 3 ms) | -0.001 ms | -0.451 ms | within +/-0.5 ms run noise | -0.378 ms |
| disk (budget 200 MB) | 44.8 MB (`du` 45M) | 45.937 MB | +1.1 MB (migration 0003 indexes + `sqlite_stat1`) | 46.1 MB (`du` 47M) |
| RSS (budget 120 MB) | 50.0 MB | 49.086 MB | -0.9 MB | 74.5 MB (see final-table note) |
| coldstart (budget 3000 ms) | 360 ms | 356.34 ms | -3.66 ms | 355 ms |
| idle CPU (budget 2 %) | 0.13 % | 0.167 % | +0.037 pp | 0.13 % |
| throughput (no section 1 budget) | 773.2 req/s, predict p95 1.555 ms | 764.8 req/s, p95 1.512 | -8.4 rps (noise) | 888.6 req/s, p95 1.588 |

Functional/plan evidence owned by this group (from the fragment): migration `[3]` rebuilt the
seven list indexes; deep keyset walk `depth=5050 pages=101` + log tail `depth=2003 pages=41`,
no duplicates, monotonic cursors; row-value cursor plans a single seek at depth 5000
(`SEARCH traces USING INDEX traces_ts ((ts_start,id)<(?,?))` vs the OR form's `SCAN`); chunked
delete probe `trace DELETE executions: {'traces': 3, 'logs': 2}`, 10 commits for 1200+600 rows;
`sqlite_stat1` populated after boot (`traces_ts: 10000 1 1`, ...); file bytes 46972928 ->
47861760 (+0.85 MB, +1.9 %); WAL 0 bytes; scoped suites 136 passed. AFTER plans in the dedicated
section below; note the after-wave-1 run also contains groups 4.5 (sse) and 4.6 (static), so
only the traces/metrics/disk rows above are attributable to storage.

### Group 4.2 recorder (wave 2 phase A, items 6-10) - fragment `evidence-4.2.md`

`bench.py overhead` (p95 of record_on - record_off, budget <= 3 ms, n=250/side):

| Run | record_on p95 | record_off p95 | overhead_added p95 | Verdict |
|---|---|---|---|---|
| BEFORE (wave-1 end, 20260923-001757) | 2.163 | 2.614 | -0.451 ms | PASS |
| AFTER 4.2 run 1 (003544) | 2.397 | 1.905 | +0.492 ms | PASS |
| AFTER 4.2 run 2 (003606) | 1.538 | 1.562 | -0.024 ms | PASS |
| AFTER 4.2 run 3 (003610) | 1.570 | 1.398 | +0.172 ms | PASS |

Run-to-run spread ~+/-0.5 ms dominates every delta vs the -0.451 before: no measurable
regression, no measurable win at harness level (kept on robustness + tracemalloc below).

`tracemalloc` snapshot (item 6, 9000 spans + 1000 traces retained, same population both sides):

```
pre-4.2 shape (no slots): retained 9000 spans + 1000 traces -> tracemalloc peak 1847040 B
4.2 item 6   (slots)     : retained 9000 spans + 1000 traces -> tracemalloc peak 1669496 B
saved = 177544 B (9.6%) over one bench-shaped population
```

Probe block (fragment section 3): slots block dynamic attrs, `vocabulary.validate` builds
exactly 1 dict/span, sampled-out path has `observations==[]` + no ring + 1 drop notice,
`ring_traces`/`ring_entries` index-snapshot semantics == pinned contract, filtered debug emits
nothing, instrumented `perf_counter` = 4 calls (2/span). Item 7 needed no change (2
`perf_counter`/span already); item 9 already applied. Scoped suites: 53 passed (recorder,
rollup, redact, vocabulary) + GREEN-29. Detailed revert-coupling in the fragment section 5.

### Group 4.1 request path (wave 2 phase B, items 1-5) - fragment `evidence-4.1.md`

| Metric | BEFORE (wave-1 end 001757) | AFTER 4.1 run 1 | AFTER 4.1 run 2 | Delta |
|---|---|---|---|---|
| overhead_added p95 | -0.451 ms | -0.010 ms (005810) | (ladder with 4.2 rows above) | no regression, PASS |
| traces deep cursor @5000 p95 | 2.859 ms | 2.350 ms (005815) | 1.998 ms (005856) | -0.509 / -0.861 ms (-17.8 / -30.1 %) |
| traces shallow p95 (after only) | 2.847 ms | 2.088 ms | 2.106 ms | worst-of PASS both runs |
| metrics_15m p95 | 1.771 ms | 1.296 ms | 1.267 ms | -0.475 / -0.504 ms (-26.8 / -28.5 %) |
| static_asset p95 (budget 5 ms) | 1.449 ms | 1.564 ms (005817) | 1.520 ms (005854) | +0.07..+0.12, flat within +/-0.1 ms box noise; PASS |
| conditional re-request | 100/100 304 | 100/100 304 | 100/100 304 | PASS |

Functional proofs: `listener nodelay: 1 / accepted nodelay: 1` + uvicorn defaults verified (no
`TCP_NODELAY` anywhere in uvicorn 0.53); db cache probe (`cached handle reused: OK`,
rollback-on-close OK, per-thread isolation, cross-thread raises, `:memory:` fresh, eviction
really closed, cache size 8); body framing smoke `413 before read / 411 / 400 / NEW chunked
413`, keep-alive two 200s one socket; byte-exact gzip pins (GREEN-29 `test_wire`). Suites:
GREEN-29, 53 scoped, full suite `1 failed, 365 passed` (the failure is the pre-existing
environmental `test_legacy_state` repo-root fixture, owned by the test-refit wave) +
`test_real_adapter` 23 passed.

### Group 4.4 engine (wave 1, items 19-23) - fragment `evidence-4.4.md`

Harness rows (all engineless by design - the FakeAdapter path cannot observe 4.4, recorded as
the R-11 backlog entry in outcome.md):

| Metric | Baseline | Final sweep | Note |
|---|---|---|---|
| throughput | 773.2 req/s, predict p95 1.555 ms | 888.6 req/s, p95 1.588 ms | engineless; real-engine evidence below |
| coldstart | 360 ms | 355 ms | engineless; real observation: server bound + healthz 200 in 962 ms with models still loading |
| overhead_added p95 | -0.001 ms | -0.378 ms | engineless; `/predict` gained one `payload.get("lang")` line - inside noise |

Real-engine evidence (own runs, RAM-guarded, port 8093): smoke `english loaded 24.7 s,
predict dept=billing conf=0.86 churn=0.82, HTTP phase full nine-span trace, exit=0`; stub
driver `PASS: 30 stub-path assertions`; real driver `PASS: 14 real-engine assertions (4 spied
forwards, all inference_mode; torch threads = min(cores,8) = 4; unpinned / model-pinned /
lang-pinned answers byte-identical to the raw agent)`; HTTP pins - three persisted traces
showing `lang.detect` absent with `lang=de`, `route.decide` absent with `model=english`, full
nine spans unpinned (documented in `docs/observability-model.md` section 2). strace capture in
the captures section below. Scoped suites 57 passed; ruff clean.

Rollback coupling: the three updated `tests/test_real_adapter.py` assertions revert together
with the group (see outcome.md).

### Group 4.5 streaming + baseline ceiling fixes + wave-2.5 SSE program - fragment `evidence-4.5.md`

SSE budget ladder (identical command each row:
`LAYA_STATE_DIR=... .venv/bin/python scripts/bench.py sse`):

| # | code state | p50 | p95 | n | verdict | result json |
|---|---|---|---|---|---|---|
| B | baseline (2026-09-22) | 32.009 | 69.031 | 12 | FAIL, 8/20 fed, 4x503 | 20260922-231552 |
| W1 | after wave 1 (4.5 items 24/26, producer threads, capacity 16->20) | 15.427 | 18.017 | 12 | FAIL, 20/20 fed, 0 rejected | 20260923-001757 |
| 0 | wave-2.5 start, no edits (re-measured) | 14.109 | 14.906 | 12 | FAIL | 20260923-012115 |
| 1 | + part 1 stream.py midpoint prefetch | 16.813 | 17.511 | 12 | FAIL (convoy alone moved nothing) | 20260923-012333 |
| 2 | + part 2 app.py loop-native handoff | 2.077 | 2.437 | 12 | PASS | 20260923-012637 |
| 3 | + part 3 bench.py single epoll stamper | 0.925 | 2.446 | 13 | PASS | 20260923-012829 |
| 4 | + part 4 TEMP stamps (one diagnostic run, then removed) | 14.056 | 14.180 | 12 | diagnostic only | 20260923-013042 |
| 5 | instrumentation removed, all parts in | 0.445 | 1.196 | 12 | PASS | 20260923-014028 |
| F | FINAL sweep (this file, wave 3) | 2.024 | 2.445 | 13 | PASS, 20/20 fed | 20260923-014652 |

Ladder attribution: part 1 (claim/wait convoy -> timer prefetch) alone = no movement; part 2
(`anyio.to_thread` per-chunk read 90.5 us x20 -> `loop.call_soon_threadsafe` event) =
17.511 -> 2.437, the win; part 3 (20 reader threads -> one epoll stamper, single clock) =
p50 2.077 -> 0.925, kills the GIL-arbitration straggler tail; part 4 diagnosed the residual
14.180 as ONE connect-phase hello-lag outlier client (registration->hello write lagged ~13 ms,
bimodal per run), not transport/app - instrumentation grep-removed afterwards (no matches).
Functional wave-1 proofs: in-process 7/7 checks (coalesced 10 events -> one 866-byte write,
shared pulse frame, dead-client drop, capacity 20), wire 9/9 on :8094 (20/20 fed, pulse cadence
median 3.00 s, first ping 15.0-15.0 s across 20/20, replay `?last_event_id`, arrival span
0.1 ms for 10 burst traces); provider cost p50 0.553 ms. Pulse-age semantics choice and the
epoll/skip-waves methodology decisions are recorded in outcome.md. Green after every part:
GREEN-29 + ruff clean.

### Group 4.6 frontend (wave 1, items 27-31) - fragment `evidence-4.6.md`

| Metric | Baseline | After 4.6 | Verdict |
|---|---|---|---|
| static_asset p95 (budget 5 ms) | 2.751 ms | 1.449 ms (wave-1 run 001757) -> 1.564 / 1.520 (4.1 runs) -> **1.531 ms final** | PASS |
| conditional re-request 304 | 100/100 | 100/100 at every run incl. final (304 p95 1.128 ms) | PASS |
| sse (re-run for the wave record) | 69.031 FAIL | 18.017 after 4.5/4.6, closed by wave-2.5 | see 4.5 ladder |

Bundle report (`scripts/web_check.py bundle`, exit 0): `26 JS files, worst gzip 71459 B;
total export 1403691 B (ceiling 3145728 B); precompressed sidecars 712660 B in 186 files
(93 .br + 93 .gz); ceilings hold`. 16 routes exported; 22 chunks = 8 shared + 14 route-specific
(item 28, runtime-proven: detail chunk fetched on demand). Items 27/29/30 confirmed
pre-existing (0 chart-library markers, polling fallback fires only after 2 failed stream
attempts and stops on reconnect, `VIRTUALIZE_AT = 500` -> 81 DOM nodes for 600 lines);
`content-visibility: auto` applied to >100-row tables (runtime: computed styles, 45 px rows,
0 zero-height, screenshot clean). Item 31: build writes 93+93 sidecars; live probes:
`Accept-Encoding: br -> content-encoding: br, vary, content-length 8068 (raw 30994)`,
gzip/br bodies byte-identical to source, identity gets `Vary` only, `.br` direct fetch -> 404,
traversal -> 404. ruff clean; `web_check catalog bundle a11y` green.

## AFTER plans (plan task 9-after; captured on `/tmp/lw-p7s43/state.sqlite3` after migration
`[3]` + `PRAGMA optimize`, deep cursor at offset 5000 by 100 keyset pages - same nine queries,
statement-for-statement against the BEFORE set above; full narrative in `evidence-4.3.md`)

### 1. traces newest - `GET /api/v1/traces?limit=50`
was: `SCAN traces USING INDEX traces_ts` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`
```
SCAN traces USING INDEX traces_ts
```
same-request span counts (unchanged): `SEARCH observations USING COVERING INDEX
observations_trace (trace_id=?)`
total_estimate: was `SCAN ... COVERING INDEX traces_ts`; after ANALYZE the planner picks the
narrower PK autoindex, still covering: `SCAN traces USING COVERING INDEX
sqlite_autoindex_traces_1`

### 2. traces by status - `GET /api/v1/traces?status=200&limit=50`
was: `SEARCH traces USING INDEX traces_status_ts (status=?)` + `USE TEMP B-TREE FOR LAST TERM
OF ORDER BY`
```
SEARCH traces USING INDEX traces_status_ts (status=?)
```
total_estimate: `SEARCH traces USING COVERING INDEX traces_status_ts (status=?)`

### 3. traces by route - `GET /api/v1/traces?route=/predict&limit=50`
was: `SEARCH traces USING INDEX traces_route_ts (route=?)` + temp b-tree
```
SEARCH traces USING INDEX traces_route_ts (route=?)
```
total_estimate: `SEARCH traces USING COVERING INDEX traces_route_ts (route=?)`

### 4. traces by model - `GET /api/v1/traces?model=english&limit=50`
was: `SEARCH traces USING INDEX traces_model_ts (model=?)` + temp b-tree
```
SEARCH traces USING INDEX traces_model_ts (model=?)       -- model is NULLable; planner uses it
```
total_estimate: `SEARCH traces USING COVERING INDEX traces_model_ts (model=?)`

### 5. traces deep keyset page - `GET /api/v1/traces?limit=50&cursor=<offset 5000>`
was: `MULTI-INDEX OR` / `SEARCH traces USING INDEX traces_ts (ts_start<?)` / `USE TEMP B-TREE
FOR ORDER BY` (cursor was `WHERE (ts_start < ? OR (ts_start = ? AND id < ?))`)
```sql
SELECT ... FROM traces WHERE (ts_start, id) < (?, ?) ORDER BY ts_start DESC, id DESC LIMIT ?
-- [1789957593.317879, '00001af9', 51]
```
```
SEARCH traces USING INDEX traces_ts ((ts_start,id)<(?,?))
```
One seek at the cursor position; cost independent of depth (item 14). For contrast, the OR form
on the same index plans `SCAN traces USING INDEX traces_ts` (depth-dependent).
total_estimate: same unfiltered `COUNT(*)` as capture 1 (`USING COVERING INDEX`).

### 6. logs tail - `GET /api/v1/logs?limit=50`
was: `SCAN log_entry USING INDEX log_ts` + temp b-tree
```
SCAN log_entry USING INDEX log_ts
```
total_estimate: `SCAN log_entry USING COVERING INDEX log_ts`
log keyset page (cursor form): `SEARCH log_entry USING INDEX log_ts (ts<?)` - seek, no sort.

### 7. observations by trace - `GET /api/v1/traces/<id>/observations`
was: `SEARCH observations USING INDEX observations_trace (trace_id=?)` + `USE TEMP B-TREE FOR
LAST TERM OF ORDER BY`
```
SELECT * FROM observations WHERE trace_id = ? ORDER BY start_ms, id   -- ['7facc654']
SEARCH observations USING INDEX observations_trace (trace_id=?)
```
No sort term; waterfall order comes straight from `(trace_id, start_ms, id)`.

### 8. audit entries - `GET /api/v1/audit?limit=50`
was: `SCAN audit_log USING INDEX audit_ts` + temp b-tree
```
SCAN audit_log USING INDEX audit_ts
```
total_estimate: `SCAN audit_log USING COVERING INDEX audit_ts`
filtered variant `?action=auth.login`: was `SCAN audit_log` + `USE TEMP B-TREE FOR ORDER BY`;
after: `SCAN audit_log USING INDEX audit_ts` (no sort term - improved for free; still no
`action` index, catalog names none -> backlog).

### 9. metrics 15 m - `GET /api/v1/metrics?...&range=15m`
unchanged from baseline (already rollup-only), captured for parity:
```
SEARCH metric_rollup USING INDEX rollup_metric_bucket
  (metric=? AND step=? AND bucket>? AND bucket<?)
```
No `traces` in the plan.

### x. `order=slowest` (not in the task-9 set; backlog)
```
SCAN traces
USE TEMP B-TREE FOR ORDER BY
```
The catalog names no `duration_ms` index, so this sort stays until the backlog item is decided.

**Summary: all nine task-9 list queries now plan as index scans/seeks with zero
`USE TEMP B-TREE` lines; every COUNT/total statement uses `USING COVERING INDEX`.**

## tracemalloc / strace captures (plan evidence-required list; sources evidence-4.2.md /
evidence-4.4.md)

Recorder slots (tracemalloc, same retained population both sides) - see the group 4.2 table
above for the numbers: 1847040 B -> 1669496 B, saved 177544 B (9.6 %).

Item 23 tokenizer/config reuse - `strace -tt -f -e trace=openat` window comparison (MARKER
timestamps from the real-engine driver; windows: boot ~0.5 s, load 44.0 s, requests 16.1 s):

| Window | Span | openat calls |
|---|---|---|
| boot (exec -> spawn marker: python + layawatch import) | ~0.5 s | 119 |
| load (spawn -> worker settled: model load + warmup) | 44.0 s | 9249 |
| **requests (worker settled -> done: 4 predicts + spy)** | **16.1 s** | **0** |

Four full predicts through the adapter open nothing: no tokenizer, no config, no `/proc`, no
SQLite. Top load-phase opens are the one-time transformers/tokenizer/`rl_agent_config.json`
families. Reproduce: `strace -tt -f -e trace=openat` around a marker-printing driver.

## FINAL DELTA TABLE (plan task 10: budget | baseline | final | delta | verdict)

Fresh final numbers from the 2026-09-23 sweep above (JSONs: `bench/results/20260923-014652.json`
and `bench/results/budget-{rss-20260923-014753, disk-20260923-014758,
coldstart-20260923-014805, idle-cpu-20260923-014820}.json`); baseline from the dated baseline
table (2026-09-22, commit `3aaaa2be`).

| Budget (target) | Baseline | Final | Delta | Verdict |
|---|---|---|---|---|
| RSS overhead (<= 120 MB) | 50.0 MB | 74.5 MB | +24.5 MB | PASS |
| Disk at 10k traces (<= 200 MB) | 44.8 MB (`du` 45M) | 46.1 MB (`du` 47M) | +1.3 MB | PASS |
| Cold start (<= 3000 ms) | 360 ms | 355 ms | -5 ms | PASS |
| Idle CPU (< 2 % / 60 s) | 0.13 % | 0.13 % | 0.00 pp | PASS |
| Recording overhead (<= 3 ms p95) | -0.001 ms | -0.378 ms | -0.377 ms | PASS |
| Trace list p95 worst-of (<= 50 ms) | 5.729 ms (deep 5.277) | 4.094 ms (deep 2.018) | -1.635 ms | PASS |
| Metrics 15 m p95 (<= 30 ms) | 2.309 ms | 1.178 ms | -1.131 ms | PASS |
| Static asset p95 (<= 5 ms, 304 when unchanged) | 2.751 ms (100/100 304) | 1.531 ms (100/100 304) | -1.220 ms | PASS |
| SSE tick p95, 20 clients (<= 5 ms) | 69.031 ms FAIL (8/20 fed, 4x503) | 2.445 ms (20/20 fed, 0 rejected) | -66.586 ms | PASS (FAIL -> PASS) |

Notes: (1) RSS rose +24.5 MB vs baseline - consistent with 4.1 item 4's per-thread cached
connections now holding one handle + the 16 MB page-cache budget per worker thread, plus the
stream producer threads from 4.5 [INFERENCE]; still 45.5 MB under budget. (2) Disk +1.3 MB is
migration 0003's widened indexes + `sqlite_stat1`, measured after every group churned the ring
buffers. (3) `traces_at_start` in the final JSON is 10170 -> 10000 by the run's own retention;
the budget verdicts are unaffected. (4) The SSE baseline FAIL was closed by the wave-2.5
program (ladder above), not by this sweep - the sweep merely confirms it at 2.445 ms.

## Artifacts (final sweep + all committed group JSONs)

- Final bench JSON: `bench/results/20260923-014652.json` (subcommand `all`: series, budgets,
  scalars, environment block).
- Final budget JSONs: `bench/results/budget-rss-20260923-014753.json`,
  `budget-disk-20260923-014758.json`, `budget-coldstart-20260923-014805.json`,
  `budget-idle-cpu-20260923-014820.json`.
- Per-group JSONs: baseline `20260922-231552` + `budget-*-20260922-2317*`; wave 1
  `20260923-001757` + `budget-*-20260923-00185*`; wave 2 `20260923-003544/003606/003610` (4.2)
  and `20260923-005810/005815/005817/005854/005856` (4.1); wave 2.5
  `20260923-012115/012333/012637/012829/013042/014028` (SSE ladder).
- Per-group fragments: `evidence-4.{1,2,3,4,5,6}.md` (kept as provenance).
- Seeded state: `/tmp/lw-bench` (throwaway, 47M; re-seedable). Live `:8050` untouched.

