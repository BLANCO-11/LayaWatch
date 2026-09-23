# Phase 7 - catalog group 4.3 (storage, docs/performance.md section 4 items 11-18) - evidence

Status: STORAGE GROUP APPLIED 2026-09-22 (wave 1, plan task 3). Baseline plans and numbers in
`plan/phase-7-optimization/evidence.md` are untouched; this file records the storage group's
functional results, the AFTER `EXPLAIN QUERY PLAN` set (same nine queries as the BEFORE capture),
the harness commands Main must run, and the marked-pending budget rows.

Files changed (storage group only):

- `layawatch/store/db.py` - full section 4.3 pragma set on every connection (item 11), new
  `optimize()` helper (item 18).
- `layawatch/store/migrations/0003_covering_list_indexes.sql` - NEW: rebuild the seven list
  indexes with their exact `ORDER BY` tiebreak column (item 13; migration needed because the
  seeded db already carries the 0001_init index shapes).
- `layawatch/store/writer.py` - pragmas moved to `db.connect` (no per-connection duplication),
  `PRAGMA optimize` after each committed batch and on shutdown (item 18).
- `layawatch/store/queries.py` - keyset cursor predicates rewritten to row-value form
  (`(ts_start, id) < (?, ?)` / `(ts, id) < (?, ?)`) = one index seek at any depth (item 14).
- `layawatch/store/retention.py` - `_DELETE_CHUNK` 1000 -> 500 (catalog's exact number, item 16),
  `PRAGMA optimize` at the end of each pass (item 18), docstrings.
- `layawatch/store/schema.sql` - reference DDL synced to the migration-0003 index shapes.
  `docs/architecture.md` section 5 carries the same seven `CREATE INDEX` lines and now drifts by
  the identical seven lines - not in this group's file list, flagged for Main (mechanical sync).

## 1. Item-by-item: change -> verification

| # | Item | Change | Verification (this file) |
|---|---|---|---|
| 11 | SQLite pragmas | `db.connect` now sets all six catalog pragmas - `journal_mode=WAL`, `synchronous=NORMAL`, `temp_store=MEMORY`, `cache_size=-16000`, `mmap_size=134217728`, `busy_timeout=5000` - plus the existing `foreign_keys=ON`. Before, only WAL+busy_timeout hit every connection and the other four lived on the writer alone; reader/retention connections never got them. The writer's four duplicate lines were removed (single source of truth). | Live read on a `db.connect` connection: `journal_mode=wal synchronous=1(NORMAL) temp_store=2(MEMORY) cache_size=-16000 mmap_size=134217728 busy_timeout=5000 foreign_keys=1`. Pending harness: `bench.py api`, `budget_check.py rss`/`disk` (see section 5). |
| 12 | Batched executemany inserts | NO CODE CHANGE NEEDED - already applied before the baseline: `Writer(batch_rows=200, batch_ms=250)` (defaults, `__main__` passes neither), one `BEGIN IMMEDIATE ... COMMIT` per batch, `executemany` for traces/observations/scores/log rows. | `layawatch/store/queries.py` header cites item 12; `tests/test_writer.py` (batch triggers, drain, no-partial-commit) in the 136-test scoped run. Pending: `bench.py overhead` + writer `write_latency_ms` counter (Main re-measures). |
| 13 | Covering indexes for the exact list queries | NEW migration `0003_covering_list_indexes.sql` rebuilds seven indexes so each one's key matches the exact `ORDER BY` of its list query (the tiebreak `id` is the second ORDER BY term of every api-reference list query; without it every plan kept `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`, the sort terms the baseline evidence flagged as "remaining work (items 13/14/18)"): `traces_ts(ts_start DESC, id DESC)`, `traces_status_ts(status, ts_start DESC, id DESC)`, `traces_route_ts(route, ts_start DESC, id DESC)`, `traces_model_ts(model, ts_start DESC, id DESC)`, `observations_trace(trace_id, start_ms, id)`, `log_ts(ts DESC, id DESC)`, `audit_ts(ts DESC, id DESC)`. Catalog's own expected effect - index-only scans for list views - is the criterion this shape meets; payload-column covering (12 trace columns per index x4) was NOT done: the catalog parenthetical names only the key columns, and payload copies would multiply write amplification against the disk budget (anti-goal 3). | AFTER plans below: no `USE TEMP B-TREE` on any task-9 list query; every `COUNT(*)` statement reports `USING COVERING INDEX`. Seed assumptions checked: `scripts/seed.py` goes through `connect()+migrate()` and has no index-name dependency, so a re-seed builds the new shapes; the already-seeded `/tmp/lw-bench` gets migration `[3]` applied by the first child boot (observed). |
| 14 | Keyset pagination only, never OFFSET | Two changes. (a) Cursor predicates in `list_traces` (newest mode) and `logs_list` rewritten from the OR form to the row-value form `(ts_start, id) < (?, ?)` / `(ts, id) < (?, ?)` - semantically identical lexicographic comparison, but with the widened index SQLite turns it into a single `SEARCH ... ((ts_start,id)<(?,?))` seek; the OR form degraded to `SCAN traces USING INDEX traces_ts` (walk from the newest end, O(depth)). (b) Audit: `grep OFFSET layawatch` finds no OFFSET outside `retention.py`'s `LIMIT -1 OFFSET ?` selectors, which are the delete-beyond-the-cap predicate (keep-newest-N), not list pagination - outside item 14's scope; item 16 covers that file. | AFTER plan 5: deep page at offset 5000 = `SEARCH traces USING INDEX traces_ts ((ts_start,id)<(?,?))` (was `MULTI-INDEX OR` + `USE TEMP B-TREE FOR ORDER BY`). Functional: full HTTP cursor walk (section 2) - 101 pages to depth 5050 with no duplicates/gaps, deep page served. Pending: `bench.py api` deep cursor (Main). |
| 15 | Writer-tick rollups + 1m/1h by retention | NO CODE CHANGE NEEDED - already applied: `Writer._rollup_rows` closes each 10 s bucket on the writer tick and cascades the increment into the 60 s and 3600 s parents (`_PARENT_STEPS`); `retention._cascade_parents` rebuilds missing/mismatched 60 s/3600 s rows before any prune; `metrics_series`/`metrics_models` read only `metric_rollup`. | AFTER plan 9: `SEARCH metric_rollup USING INDEX rollup_metric_bucket`, no `traces` in the plan (acceptance 7's "no traces scan" holds, unchanged). Probe run (section 2) shows the retention pass building pending 10 s rows + parents before pruning (item 15 mechanism live). Pending: `bench.py api` metrics row. |
| 16 | Chunked retention deletes, 500 rows per statement | `_DELETE_CHUNK` 1000 -> 500 (catalog's exact number; all five prune/update loops already ran `LIMIT <chunk>` + `conn.commit()` per chunk). | Statement-count probe on a scratch db: 1200 old traces + 600 old logs through `retention.apply(max_traces=0, max_logs=0, ...)`: `trace DELETE executions: {'traces': 3, 'logs': 2}` = ceil(1200/500) + ceil(600/500), 10 commits during the pass (one per chunk plus build/final/vacuum phases), both tables empty, `traces_pruned=1200 logs_pruned=600`. Chunk boundaries = separate committed statements, so no long write lock. Pending: writer latency during a live prune (Main's bench/counter). |
| 17 | Idle `wal_checkpoint(TRUNCATE)` + 20 % VACUUM guard | NO CODE CHANGE NEEDED - already applied and wired: `retention._checkpoint` runs `wal_checkpoint(TRUNCATE)` only when `queue_idle()` reports an empty writer queue (`__main__` passes `lambda: writer.counters()["write_queue_depth"] == 0`); `_maybe_vacuum` runs `VACUUM` only when `free_bytes * 100 > total_bytes * free_page_pct` with `free_page_pct=20` default. | `tests/test_retention.py` covers idle-vs-busy checkpoint and the vacuum guard (136-test run); the section-2 probe pass reports `wal_checkpointed=1 vacuumed=1`; `/tmp/lw-p7s43/state.sqlite3-wal` = 0 bytes after the session. Pending: `budget_check.py disk` (Main). |
| 18 | `PRAGMA optimize` after bulk writes and on shutdown | NEW `db.optimize(conn)` (best-effort, swallows `sqlite3.Error` so stats refresh can never fail a committed batch) called: (a) in `Writer._commit` after each successful `COMMIT`, (b) in `Writer._run` finally block before `conn.close()` = process shutdown, (c) at the end of every `retention.apply` (the bulk deletes). | After booting the instance, `sqlite_stat1` exists with real stats (`traces_ts: 10000 1 1`, `traces_status_ts: 10000 582 1 1`, `observations_trace: 85475 9 1 1`, ...); AFTER plans below were captured with those stats present. Shutdown log (section 2) is clean - the writer's shutdown optimize ran without a warning. |

## 2. Functional verification (own instance, own copy)

Setup: `cp -r /tmp/lw-bench /tmp/lw-p7s43` (seeded db,46972928 bytes before), then
`LAYA_PORT=8092 LAYA_STATE_DIR=/tmp/lw-p7s43 .venv/bin/python -c "import sys;
sys.modules['laya'] = None; from layawatch.__main__ import main; raise SystemExit(main())"`
(engineless fake adapter, the budget_check launch trick; `laya` is installed in `.venv`, so the
null-module trick is required). The live server on `:8050` was never touched; my instance was
stopped when done.

```
$ hub start p7s-store -> ready pid=398471 uptime=1.5s   # boot incl. migration [3] + first retention pass
layawatch migrations applied: [3]
layawatch laya package not importable; serving with the fake engine (device=fake)
layawatch layawatch starting on http://127.0.0.1:8092 (web_root=web/out)
```

Endpoints exercised after migration (the migration applies on boot, one-time index rebuild):

```
GET /api/v1/traces?limit=3            http=200 bytes=918
GET /api/v1/traces?status=200&limit=3 http=200 bytes=917
GET /api/v1/traces?route=/predict     http=200 bytes=917
GET /api/v1/traces?model=english      http=200 bytes=917
GET /api/v1/logs?limit=3              http=200 bytes=553
GET /api/v1/audit?limit=3             http=401 (admin+ endpoint, auth unchanged - SQL captured via EXPLAIN below)
GET /api/v1/traces/<id>/observations  http=200 bytes=1090
GET /api/v1/metrics?...&range=15m     http=200 bytes=9715
```

Deep keyset walk over HTTP with the rewritten row-value predicate (cursor at depth >= 5000, then
one more page behind it; the log tail walked to its end):

```
traces: walked depth=5050 pages=101 (deep page behind offset>=5000 served)
logs: walked full tail depth=2003 pages=41
pagination OK: no duplicates, monotonic ts, cursors end to end
```

Chunked-delete probe (item 16): `trace DELETE executions: {'traces': 3, 'logs': 2} | commits
during the pass: 10` for 1200+600 rows, counts exact, tables empty.

State after the session: `du -sh /tmp/lw-p7s43` = **46M** (baseline 45M; +1 MB = widened indexes
+ `sqlite_stat1`), file bytes 46972928 -> 47861760 (+0.85 MB, +1.9 %), WAL truncated to 0 bytes,
row counts unchanged (traces 10000, observations 85475, log_entry 2000, audit_log 400,
metric_rollup 195078).

Scoped tests + lint (after all edits, no other group's files):

```
$ .venv/bin/python -m pytest tests/test_queries.py tests/test_rollup.py tests/test_writer.py \
    tests/test_retention.py tests/test_migrations.py tests/test_queries_read.py \
    tests/test_traces_api.py tests/test_logs_api.py tests/test_metrics_api.py -q
136 passed in 10.16s

$ .venv/bin/ruff check --select E4,E7,E9,F,I layawatch/store/{db,queries,writer,retention}.py
All checks passed!   (exit=0)
```

The first five files are the assigned set; the four API/query files are added because
`queries.py` changed (they are its behavioral tests). No test additions were written - tests are
wave 3 per the wave rules.

## 3. AFTER plans - same nine task-9 queries as the BEFORE capture in evidence.md

Captured on `/tmp/lw-p7s43/state.sqlite3` after migration `[3]` and `PRAGMA optimize` (item 18);
deep cursor at offset 5000 reached by 100 keyset pages, like the BEFORE capture. "was" lines are
the baseline plans from evidence.md.

### 1. traces newest - `GET /api/v1/traces?limit=50`
was: `SCAN traces USING INDEX traces_ts` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`

```
SCAN traces USING INDEX traces_ts
```
same-request span counts (unchanged, already covering):
```
SEARCH observations USING COVERING INDEX observations_trace (trace_id=?)
```
total_estimate: was `SCAN traces USING COVERING INDEX traces_ts`; after ANALYZE the planner picks
the narrower PK autoindex, still covering:
```
SCAN traces USING COVERING INDEX sqlite_autoindex_traces_1
```

### 2. traces by status - `GET /api/v1/traces?status=200&limit=50`
was: `SEARCH traces USING INDEX traces_status_ts (status=?)` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`
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
was: `MULTI-INDEX OR / SEARCH traces USING INDEX traces_ts (ts_start<?) / ... USE TEMP B-TREE FOR ORDER BY`
(cursor was `WHERE (ts_start < ? OR (ts_start = ? AND id < ?))`)

```
SELECT ... FROM traces WHERE (ts_start, id) < (?, ?) ORDER BY ts_start DESC, id DESC LIMIT ?
-- [1789957593.317879, '00001af9', 51]
SEARCH traces USING INDEX traces_ts ((ts_start,id)<(?,?))
```
One seek at the cursor position; total cost independent of depth (item 14's expected effect).
For contrast, the OR form on the same index plans `SCAN traces USING INDEX traces_ts` (depth-
dependent), which is why the predicate was rewritten.
total_estimate: same unfiltered `COUNT(*)` as capture 1 (`USING COVERING INDEX`).

### 6. logs tail - `GET /api/v1/logs?limit=50`
was: `SCAN log_entry USING INDEX log_ts` + temp b-tree

```
SELECT id, ts, level, source, trace_id, message FROM log_entry WHERE 1
  ORDER BY ts DESC, id DESC LIMIT ?   -- [51]
SCAN log_entry USING INDEX log_ts
```
total_estimate: `SCAN log_entry USING COVERING INDEX log_ts`
log keyset page (cursor form): `SEARCH log_entry USING INDEX log_ts (ts<?)` - seek, no sort.

### 7. observations by trace - `GET /api/v1/traces/<id>/observations`
was: `SEARCH observations USING INDEX observations_trace (trace_id=?)` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`

```
SELECT * FROM observations WHERE trace_id = ? ORDER BY start_ms, id   -- ['7facc654']
SEARCH observations USING INDEX observations_trace (trace_id=?)
```
No sort term; waterfall order comes straight from `(trace_id, start_ms, id)`.

### 8. audit entries - `GET /api/v1/audit?limit=50`
was: `SCAN audit_log USING INDEX audit_ts` + temp b-tree

```
SELECT id, ts, actor_id, actor, action, target, result, meta FROM audit_log WHERE 1
  ORDER BY ts DESC, id DESC LIMIT ?   -- [51]
SCAN audit_log USING INDEX audit_ts
```
total_estimate: `SCAN audit_log USING COVERING INDEX audit_ts`
filtered variant `?action=auth.login`: was `SCAN audit_log` + `USE TEMP B-TREE FOR ORDER BY`;
after: `SCAN audit_log USING INDEX audit_ts` (no sort term - improved for free; there is still no
`action` index because the catalog does not name one - see backlog).

### 9. metrics 15 m - `GET /api/v1/metrics?metrics=requests,latency,errors,queue&range=15m`
unchanged from baseline (already rollup-only), captured for parity:
```
SEARCH metric_rollup USING INDEX rollup_metric_bucket
  (metric=? AND step=? AND bucket>? AND bucket<?)
```
No `traces` in the plan.

### x. `order=slowest` (not in the task-9 set; recorded for the backlog)
```
SCAN traces
USE TEMP B-TREE FOR ORDER BY
```
The catalog names no `duration_ms` index, so this sort stays until the backlog item is decided.

**Summary: all nine task-9 list queries now plan as index scans/seeks with zero
`USE TEMP B-TREE` lines; every COUNT/total statement uses `USING COVERING INDEX`.**

## 4. Commands for Main (run sequentially after the wave; children boot engineless and apply
migration 0003 on first boot against `/tmp/lw-bench`)

```
# required by this group's acceptance:
LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py api
LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py disk
# storage also affects these (pragma set now hits every connection; writer runs optimize
# per batch; first boot carries the one-time index rebuild) - Main re-measures:
LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py rss
LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py coldstart
LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py overhead
```

Notes for Main: first child boot on the shared seeded db prints `migrations applied: [3]`
(one-time index rebuild; observed total boot-to-ready 1.5 s on this box, inside the 3000 ms
cold-start budget, but the canonical number is `budget_check.py coldstart`). A re-seed is NOT
required (`seed.py` = `connect()+migrate()`); if you do re-seed, rollup values drift ~0.02 % by
wall clock per evidence.md. The EXPLAIN capture script used here was `/tmp/lw_p7s_explain.py`
(throwaway; the SQL and plans in section 3 are the durable record, matching evidence.md's
BEFORE set statement-for-statement).

## 5. Pending BEFORE/AFTER budget rows (Main fills AFTER; baseline from evidence.md)

| Budget | Target | BEFORE (baseline) | AFTER | Status |
|---|---|---|---|---|
| Trace list query, 10k traces | <= 50 ms p95 | 5.729 ms p95 (worst of shallow/deep) | `bench.py api` deep cursor + shallow | **PENDING Main** |
| Metrics query, 15 m window | <= 30 ms p95 | 2.309 ms p95 | `bench.py api` metrics_15m | **PENDING Main** |
| LayaWatch disk at 10k traces | <= 200 MB | 44.8 MB (`du` 45M) | group copy observed 46M / +0.85 MB file bytes; canonical `budget_check.py disk` | **PENDING Main** |
| LayaWatch RSS overhead | <= 120 MB | 50.0 MB | reader connections now hold the 16 MB cache + 128 MB mmap budget | **PENDING Main** (`budget_check.py rss`) |
| Recording overhead, engine | <= 3 ms p95 added | -0.001 ms p95 | writer adds `PRAGMA optimize` per committed batch | **PENDING Main** (`bench.py overhead` + writer latency counter) |
| Cold start | <= 3000 ms | 360 ms | one-time migration [3] index rebuild on first boot | **PENDING Main** (`budget_check.py coldstart`) |
| Idle CPU | < 2 % / 60 s | 0.13 % | no cadence change (retention still 60 s, writer unchanged when idle) | no expected effect; re-run at final sweep |
| Static asset / SSE tick | 5 ms / 5 ms | 2.751 ms / 69.031 ms FAIL | storage group does not touch these paths | not affected |

## 6. Notes and backlog (catalog is binding - these are NOT applied)

1. `duration_ms` index for `order=slowest` list (the only remaining temp b-tree): not named in
   item 13's parenthetical - backlog.
2. `audit_log(action, ts)` index for the filtered audit variant: not named in the catalog -
   backlog (its plan already improved from table scan + sort to index scan + no sort).
3. Payload-covering indexes (all 12 list columns inside each trace index): rejected against the
   catalog's own column list and the disk/write-amplification cost - would revisit only if a
   measured list query still misses budget.
4. `docs/architecture.md` section 5 index DDL needs the same seven-line sync as `schema.sql`
   (flagged above; outside this group's file list).
5. Retention's `LIMIT -1 OFFSET ?` selectors are keep-newest-N delete predicates, not list
   pagination; item 14's "never OFFSET" governs the api-reference list queries, which contain
   none. Converting the delete selector to a boundary-row keyset would change tie-breaking
   behavior for no measured win - not done.
6. `metrics_summary` (KPI strip) still reads `traces` for its exact small-window numbers
   (documented exactness in api-reference section 6); the charted series/models are rollup-only.
   Rewriting it would weaken a documented guarantee (anti-goal 3) - not done.
7. Live retention cycles log counts only on failure; the per-pass counts line exists in
   `seed.py` output ("seed: retention pass {...}"). Adding a per-cycle production log line was
   not required by the catalog text - backlog if Main wants item 16's "retention log lines"
   verify to fire in server logs.
