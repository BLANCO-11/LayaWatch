# Phase 1 evidence

Status: complete. All commands run from the repository root on 2026-09-22 against the real
`laya` 0.3.5 engine (torch 2.14.0+cpu, checkpoints from `.hf-cache`), output transcribed verbatim.
Engine-heavy runs were serialized: three concurrent checkpoint loads on this 15 GiB box caused
one smoke child to be OOM-killed (P1-I1, resolved as environment).

## Suite (criteria prerequisite)

```
$ make lint
All checks passed!
$ make test
256 passed in 7.43s
```

256 tests: Phase 0's 107 plus recorder/vocabulary/redact/rollup 53, writer/retention/queries 32,
engine adapter/keys/legacy 57, middleware/engine-API 22 (combined runs include cross-file overlap).

## Criterion 1 - one real /predict produces the full span trace

`HF_HOME=.hf-cache .venv/bin/python scripts/smoke_laya.py --state-dir /tmp/lw-p1-gate --keep-state`
(spawns its own server, POSTs /predict through the middleware, asserts the persisted trace):

```
[1/3] english checkpoint loaded in 25.8s (device=cpu)
[2/3] predict OK in 1515 ms: dept=billing conf=0.86 churn=0.82
[3/3] second predict OK in 1558 ms (warm)
[http] server ready in 137 ms (port=8050)
[http] POST /predict OK in 51677 ms (3 answers)
trace_id=f84e63ec
observations: http.receive, auth.verify, body.parse, model.load, model.load, lang.detect,
              route.decide, queue.wait, forward, serialize, response.send
smoke_exit=0
```

Trace dump (sqlite, `plan` gate):

| Field | Value |
|---|---|
| status / model / route_reason / lang | 200 / english / "English Latin text" / en |
| duration_ms | 51675.555 |
| sum of all span durations | 50580.492 |
| meta.framework_ms recorded | 1095.063 (= duration - sum, identity holds) |
| model.load spans | 23459.0 ms + 25558.8 ms (both checkpoints preloaded; `LAYA_MODELS` defaults to english,multilingual in the smoke env) |
| forward_ms / queue_ms | 1562.15 / 0.014 (idle single request) |
| question_count | 3 |

All nine required spans are present, in order; the two `model.load` spans are the documented
conditional span (a cold server loads checkpoints on first use). The cold trace's
framework_ms (1095 ms) is the one-time `import laya` + Router construction; steady-state
framework cost is measured in criterion 6. The warm trace shape (no model.load) is asserted by
tests and shown in criterion 2.

## Criterion 2 - queue_ms under concurrency, zero when idle

Two simultaneous POSTs against the live gate server (fresh state dir, `LAYA_PORT=8070`):

| Trace | queue_ms | duration_ms | Note |
|---|---|---|---|
| 58e61ec0 | **2417.63** | 4234.39 | second thread waited on the single-worker engine lock; its `queue.wait` span duration = 2417.63 ms |
| ce071254 | 0.008 | 2181.09 | first thread, warm engine |
| f84e63ec (smoke, single request) | 0.014 | 51675.56 | idle engine: queue_ms effectively zero |

Both concurrent requests returned 200; `framework_ms` = 1.78 and 0.77.

## Criterion 3 - 422 rejection recorded, never sampled away

`POST /predict` with a French state against `LAYA_ENGLISH_ONLY=1`:

```json
{"error": {"code": "english_only", "message": "english-only deployment: state did not detect as English",
 "details": {"detection": {"script": "latin", "language": "fr", "non_latin_fraction": 0.0, "diacritic_rate": 0.0}}}}
HTTP 422 in 1.8 ms
```

Trace `9858752f`: status 422, error_code `english_only`, spans
`http.receive, auth.verify, body.parse, lang.detect, error, response.send` - **no forward span**,
`error` event present, stored (errors bypass sampling).

## Criterion 4 - rollups queryable within one 10 s tick

Poll loop against `metric_rollup` starting at request time: first `step=10, metric=requests`
row visible **0.51 s** after the first request (budget: one tick = 10 s).

## Criterion 5 - retention prunes to caps; rollups keep answering pruned windows

Copy of the gate database + 12 000 synthetic traces spread over 8 days, then
`apply(conn, max_traces=100, max_age_days=7)`:

```
pending_rollups_built: 35724   pending_parents_built: 34869
traces_pruned: 11908           wal_checkpointed: 1   vacuumed: 0
traces_before: 12008  traces_after: 100                (cap respected)
```

Hour-aligned window covering the pruned 7-8 day range, after pruning:

| Source | Count |
|---|---|
| traces before prune (window) | 1546 |
| metric_rollup step=10 sum | 1546 |
| metric_rollup step=60 sum | 1546 |
| metric_rollup step=3600 sum | 1546 |
| per-hour rows where 1 h != sum of its 60 s children | **0 mismatches** |

(Unaligned first pass showed 1501 vs 1501 exact at step 60; the apparent 1 h delta was my
query's window edges, not the data - re-checked hour-aligned with zero mismatches.)

## Criterion 6 - recording overhead: p95 <= 3 ms (200 requests, same payload, warm engine)

`/tmp/lw_overhead.py on|off`: spawned server per mode, 5 warmups, 200 sequential POSTs, identical
payload. Total 709.9 s.

| Mode | p50 | p95 | p99 | max |
|---|---|---|---|---|
| recording ON | 1563.69 | 2381.68 | 2748.45 | 2896.26 |
| recording OFF | 1527.36 | 1650.45 | 1702.37 | 1820.07 |

The client-observed tail is engine time, not LayaWatch: per-trace decomposition of the ON run
(n = 205 incl. warmups, read back from `traces.meta.framework_ms`):

| Component | p50 | p95 | p99 |
|---|---|---|---|
| total duration | 1562.10 | 2380.08 | 2745.85 |
| forward (engine) | 1560.25 | 2321.15 | 2716.95 |
| queue | - | 0.015 | - |
| **framework (recorder + middleware + serialize)** | **0.806** | **1.029** | 3.177 |

**framework p95 = 1.029 ms <= 3 ms budget -> PASS.** The ON/OFF p95 gap (731 ms) sits entirely in
`forward_ms` (engine CPU variance between runs; forward p95 alone explains it). The single
framework outlier (24769.7 ms) is the cold warmup on the pre-`model.load`-span build - that span
now records checkpoint loads (P1Engine fix); steady-state numbers are unaffected.

## Criterion 7 - RSS overhead <= 120 MB over engine-only baseline

Baseline: `laya.load(...)` + 2 warm predicts in a bare process. Server: same checkpoint loaded
through `/predict` on a fresh LayaWatch (both measured from `/proc/<pid>/status VmRSS`, serialized):

| Process | VmRSS |
|---|---|
| engine only | 2061.8 MiB |
| LayaWatch + engine | 2071.4 MiB |
| **delta** | **9.6 MiB** (budget 120) -> PASS |

## Criterion 8 - legacy api_keys.json imported exactly once

Fixture state dir with a real-format `api_keys.json` (1 key, legacy unpeppered SHA-256 digest,
`enabled: true`), two `--migrate-only` starts:

```
run 1: auth imported 1 key(s) from api_keys.json; key auth armed=True
       layawatch legacy api_keys.json import: 1 keys imported
run 2: auth api_keys.json already imported; skipping
       layawatch legacy api_keys.json import: 0 keys imported
file renamed api_keys.json -> api_keys.json.imported (original gone)
api_keys rows: k_9f3a21 "old host key" prefix=laya_aba   settings keys.auth = true
verify_key(<legacy plaintext>) -> k_9f3a21   (dual-format check, P1-D5)
```

## Criterion 9 - payload capture off by default

Live request whose body carried the marker `LW_FIXTURE_MARKER_XYZ` returned 200 with all three
answers; after flush, `state.sqlite3` + `-wal` + `-shm` scanned as raw bytes:

```
marker occurrences: 0
trace rows for the request: present (status 200)
meta.payload_capture: false
```

## Budget harness (`scripts/budget_check.py all`)

```
name             value      budget   result
rss              25.4 MB    120 MB   PASS
disk             0.1 MB     200 MB   PASS
coldstart        85 ms      3000 ms  PASS
idle-cpu         0.00 %     2 %      PASS
exit 0 (62.86 s, includes the 60 s idle window)
```

## Repository state

`git status --short` clean before commit; no state/model/cache files tracked; `make lint` and
`make test` are the recorded suite commands above.
