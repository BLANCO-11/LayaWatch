# Phase 7 evidence - catalog group 4.4, engine interaction (items 19-23)

Status: FUNCTIONAL VERIFICATION DONE 2026-09-23, by P7Engine. Harness rows for Main are marked
PENDING (Main runs `bench.py`/`budget_check.py` sequentially after the wave for clean
attribution). Baseline commit `3aaaa2be` lives in `plan/phase-7-optimization/evidence.md`.

Rule of this file: commands plus observed output, per `docs/performance.md` section 1.

## Scope - files touched (all five approved by Main mid-wave)

| File | Change |
|---|---|
| `layawatch/engine/adapter.py` | items 19-23 (details per item below) |
| `docs/observability-model.md` | section 2: the two absent-by-design spans documented |
| `layawatch/api/engine.py` | one line: forward `lang=_param(payload, "lang")` to `predict` |
| `layawatch/api/playground.py` | one line: same plumbing |
| `tests/test_real_adapter.py` | 3 pre-catalog assertions updated (see Rollback) |

## Environment and RAM guard (P1-I3)

Same box as the baseline: Linux 6.8.0-1064-azure, AMD EPYC 7763, `nproc`=4 CPUs, MemTotal
15.61 GiB, no swap. Live server on `:8050` (holds ~3.4 GB RSS) was never touched. Every
real-engine run below was SERIALIZED behind a fresh MemAvailable check; no two of my engines
ever ran at once; port 8093 only (engine-group port):

| Checkpoint | MemAvailable | Need (est.) | Verdict |
|---|---|---|---|
| before `smoke_laya.py` | 6.67 GiB | 4.5 GiB (parent ~1.5 + child ~3.0) | GO |
| before strace driver | 6.70 GiB | 1.8 GiB (LAYA_MODELS=english only) | GO |
| before pin server | 6.68 GiB | 3.5 GiB (two checkpoints ~2.9 GB) | GO |

Checkpoints came from the local caches only (`.hf-cache` via smoke's `HF_HOME` default, and
`~/.cache/huggingface/hub`); no network fetch was needed or attempted.

## Item-by-item disposition (every item applied; none skipped)

| # | Catalog item | Disposition | Proof |
|---|---|---|---|
| 19 | `torch.inference_mode()` around every forward + `torch.set_num_threads(min(cores,8))` | APPLIED in adapter: predict and warmup forwards wrapped in `torch.inference_mode()` (resolved once in `_configure_torch` on the real path; injected engines get `nullcontext`); threads capped at `min(sched affinity, 8)` = 4 on this box, applied once when torch is first imported under the ensure lock | driver asserts below: 4 spied forwards all `torch.is_inference_mode_enabled()==True`, `torch.get_num_threads()==4` |
| 20 | One warmup pass per checkpoint at startup | APPLIED: production `RealAdapter` (no injected engine) starts a startup worker at construction; it builds/stages the engine, then runs one warm forward per resident checkpoint under the predict lock; each checkpoint warmed exactly once ever (`_warmed`) | startup log lines below (real engine, two servers); stub asserts "exactly one warm pass per resident checkpoint" and "never re-warms" |
| 21 | Skip `lang.detect` when client pins `lang=`, skip `route.decide` when `model=` explicit | APPLIED: pinned `lang` runs zero `analyse` calls and emits no `lang.detect` (trace carries the pin) - except `english_only`, where detection still gates the 422 refusal and still records; explicit `model=` computes the decision once, untimed, with no `route.decide` span; pin plumbed through the protocol, both handlers and FakeAdapter | stub assertions + the three HTTP traces below; documented in `docs/observability-model.md` section 2 |
| 22 | Background prefetch only when `LAYA_MODELS` lists it + idle CPU + RAM guard | APPLIED: loading stages - first configured checkpoint synchronously (its `model.load` span as before), the rest queue; only the production worker drains the queue, gated per item on (a) membership in the normalised `LAYA_MODELS` set (structurally true: the queue is built only from it; checked at runtime), (b) 1-min load average leaving a spare core of `cores`, (c) `MemAvailable >= GB_PER_CHECKPOINT` (same floor as `load()`), and non-blocking lifecycle lock (admin op in flight defers). Admin `unload` cancels queued entries. Failures/skips log to the `engine` source; no polling loop (a `predict` re-kicks a gated-off worker, and the kick pre-gates silently so a starved box pays nothing per request) | stub: RAM-guard skip line + queue intact; GO: drain + `prefetched multilingual` line; real engine: prefetch completed (30.7 s) and warmed the second checkpoint |
| 23 | Reuse tokenizer/config per checkpoint, no config re-read per request | ALREADY SATISFIED BY CONSTRUCTION - verified, no code change needed (catalog itself says "already loaded"): `Agent` reads tokenizer/`rl_agent_config.json` once in `__init__`; `system_one` uses the in-memory `tok`/`cfg`; the adapter performs no per-request file I/O (`_checkpoint_location` runs only inside a `model.load` span) | `strace` window comparison below: 0 `openat` during 4 predicts vs 9,249 during load+warmup |

## 1. Smoke test, engine path - predictions identical for pinned inputs

RAM guard GO (6.67 GiB); serialized; `--http-port 8093`; child state kept for log capture.

```
$ .venv/bin/python scripts/smoke_laya.py --http-port 8093 --keep-state
[1/3] english checkpoint loaded in 24.7s (device=cpu)
[2/3] predict OK in 1601 ms: dept=billing conf=0.86 churn=0.82
[3/3] second predict OK in 1562 ms (warm)
PASS: single-checkpoint CPU hosting works (load 24.7s, warm 1562 ms)
[http] server ready in 530 ms (port=8093 state=/tmp/lw-smoke-510dxogx)
[http] POST /predict OK in 38889 ms (3 answers)
trace_id=d0fe346b
observations: http.receive, auth.verify, body.parse, lang.detect, route.decide,
              queue.wait, forward, serialize, response.send
exit=0
```

- The engine phase pins the fixture `STATE`/`QUESTIONS` and gets `dept=billing conf=0.86
  churn=0.82` - the same assertion set the raw laya path has always answered (`expect_choice=
  "billing"`, ranges). The HTTP phase ran through the changed adapter and produced the full
  nine-span trace (unpinned request: nothing skipped).
- First HTTP predict paid the cold model load (38.9 s of the 60 s timeout) exactly as before
  the change - the load now overlaps the listener (`server ready in 530 ms`), but the request
  still waits for the checkpoint; no latency was hidden, none added.
- The 33 s between server start and `warmed english` is the child's own english load + warmup
  in the background worker.

Child startup log (`/tmp/lw-smoke-510dxogx/server.log`, default level=info):

```
2026-09-22 23:52:08 layawatch engine: real adapter (models=english,multilingual, device=auto)
2026-09-22 23:52:08 layawatch layawatch starting on http://127.0.0.1:8093 (web_root=web/out)
2026-09-22 23:52:41 engine engine: warmed english in 524 ms
Fetching 5 files: 100%|...|5/5 [00:00<00:00, 1770.65it/s]      <- multilingual prefetch began,
                                                                   then smoke stopped the server
```

## 2. Stub-engine functional run (no RAM, no torch)

`PYTHONPATH=<repo> .venv/bin/python /tmp/verify44_stub.py` (throwaway script; outputs pasted
verbatim, script removed at cleanup):

```
2026-09-22 23:50:47 engine engine: warmed english in 0 ms
2026-09-22 23:50:47 engine engine: prefetch of multilingual deferred (MemAvailable 1.5 GB < 1.5 GB)
2026-09-22 23:50:47 engine engine: prefetched multilingual in 0 ms
2026-09-22 23:50:47 engine engine: warmed multilingual in 0 ms
PASS: 30 stub-path assertions
```

Highlights of the 30: staged first-sync/second-queued; RAM-guard defer keeps the queue and
logs at `debug` on source `engine`; gates-pass drains, warms each checkpoint exactly once,
logs the prefetch event; idempotent re-run adds no warm and no prefetch; lang pin =200,
`lang`=`de`, routed to `multilingual`, reason `explicit lang='de'`, `lang.detect` ABSENT,
`route.decide` present, `analyse` called 0 times; model pin = reason `explicit model=...`,
`route.decide` ABSENT, `lang.detect` present, `analyse` exactly2; unpinned = full nine-span
flow; admin unload cancels a queued prefetch and leaves unrelated entries; `torch` never
enters the process on the stub path.

## 3. Real-engine driver - threads, inference_mode, pinned equivalence

RAM guard GO (6.70 GiB); `LAYA_MODELS=english` (one checkpoint); run as strace's direct child
so ptrace is legal:

```
$ strace -tt -f -e trace=openat -o /tmp/trace44.log \
      env PYTHONPATH=<repo> LAYA_MODELS=english .venv/bin/python /tmp/verify44_real.py
MARKER spawn 1790121300.647210
MARKER load_done 1790121344.467607
  ok - loaded exactly the LAYA_MODELS checkpoint
  ok - prefetch queue empty (nothing outside LAYA_MODELS)
  ok - warmup pass ran once at startup
  ok - torch threads = min(cores, 8) =4
  ok - unpinned answers identical to raw agent
  ok - model=english pinned answers identical to raw agent
  ok - lang=en pinned answers identical to raw agent
  ok - department choice identical (billing)
  ok - model pin reason = laya explicit-model wording
  ok - lang pin reason = laya explicit-lang wording
  ok - lang pin result lang = pin
  ok - unpinned reason unchanged
  ok - deterministic repeat
MARKER requests_done 1790121360.554046
  ok - every spied forward ran under inference_mode
PASS:14 real-engine assertions (4 spied forwards)
exit=0
```

"identical to raw agent" compares the adapter's `answers` dict byte-for-byte against the same
checkpoint's own `system_one(STATE, QUESTIONS)` on the same inputs - unpinned, `model=`-pinned
and `lang=`-pinned all equal. Engine correctness unchanged.

## 4. strace -c-style window comparison (item23 verify column)

Windows from the MARKER timestamps above (`-tt`, `-f`, `openat` only):

| Window | Span | openat calls |
|---|---|---|
| boot (exec -> spawn marker: python + layawatch import) | ~0.5 s |119 |
| load (spawn -> worker settled: model load + warmup) |44.0 s |9249 |
| **requests (worker settled -> done:4 predicts + spy)** | **16.1 s** | **0** |

Top load-phase opens are exactly the one-time files: transformers model/auto/modernbert
Python sources, tokenizer and `rl_agent_config.json` families. The request phase - four full
predicts through the adapter - opens nothing: no tokenizer, no config, no `/proc`, no SQLite.
Reproduce: any `strace -tt -f -e trace=openat` around a driver that prints time markers before
and after its predicts.

## 5. HTTP end-to-end pins (handler plumbing + absent spans)

Server: `LAYA_STATE_DIR=/tmp/lw-p44 LAYA_PORT=8093 LAYWATCH_LOG_LEVEL=debug .venv/bin/python
-m layawatch` (hub-managed, stopped afterwards). Startup log (debug level):

```
2026-09-22 23:58:07 layawatch engine: real adapter (models=english,multilingual, device=auto)
2026-09-22 23:58:07 layawatch layawatch starting on http://127.0.0.1:8093 (web_root=web/out)
2026-09-22 23:58:42 engine engine: warmed english in385 ms
2026-09-22 23:59:12 engine engine: prefetched multilingual in30683 ms
2026-09-22 23:59:12 engine engine: warmed multilingual in197 ms
```

(RAM guard GO6.68 GiB; box idle at boot, loadavg0.65; healthz answered in962 ms while models
were still loading - staging keeps loads off the bind path.)

Three `/predict` posts (`lang=de`, `model=english`, unpinned), then the persisted traces:

```
a5e9f88c lang=de model=multilingual reason=explicit lang='de' status=200
  -> http.receive, auth.verify, body.parse, route.decide, queue.wait, forward, serialize,
     response.send        (NO lang.detect - absent by design)
6751fba7 lang=en model=english reason=explicit model='english' status=200
  -> http.receive, auth.verify, body.parse, lang.detect, queue.wait, forward, serialize,
     response.send        (NO route.decide - absent by design)
c572913e (unpinned) lang=en model=english reason=English Latin text status=200
  -> http.receive, auth.verify, body.parse, lang.detect, route.decide, queue.wait, forward,
     serialize, response.send   (full nine)
```

The `lang=de` trace proves detection never ran end-to-end (zero `lang.detect` observation) yet
the pin drove routing, the trace `lang` field and the response. The `model=english` trace
proves `route.decide` is gone while detection still records.

## 6. Scoped tests and lint

```
$ .venv/bin/python -m pytest tests/test_adapter.py tests/test_engine_api.py \
      tests/test_models_api.py tests/test_real_adapter.py -q
57 passed in1.88s

$ .venv/bin/ruff check --target-version py312 --select E4,E7,E9,F,I \
      layawatch/engine/adapter.py layawatch/api/engine.py layawatch/api/playground.py \
      tests/test_real_adapter.py
All checks passed!
```

(The assignment's `tests/test_models.py` does not exist; the models file is
`tests/test_models_api.py`, run above. `tests/test_real_adapter.py` is the direct contract
suite for the file I own, so it is run as scoped proof.)

## Pending BEFORE/AFTER rows for Main (do not fill in by hand - run the commands)

| Budget | Baseline (evidence.md) | After 4.4 | Command for Main | Note |
|---|---|---|---|---|
| Throughput (no section1 budget; recorded for4.4) |773.2 req/s, p951.555 ms, n=300 | PENDING | `LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py throughput` | The harness boots ENGINELESS (FakeAdapter) by design, and4.4 touches only the RealAdapter path - expect noise-level delta; the engine effect is sections1-4 above. FakeAdapter did gain the optional `lang` kwarg (default None, unpinned behavior byte-identical). |
| Cold start to first served request |360 ms | PENDING | `LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py coldstart` | Also engineless, so expect no movement. Real-engine observation: pin server bound + healthz200 in962 ms with models still loading (section5). |
| Recording overhead on engine latency | -0.001 ms p95 added | PENDING | `LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py overhead` | The /predict handler gained one `payload.get("lang")` (plumbing line); engineless harness - expect delta inside noise. |

Budgets not affected by this group: rss, disk, idle-cpu (all engineless), api, static, sse
(no touched code on those paths).

## observability-model.md diff summary (which spans are now by-design absent)

One paragraph added to section 2, right after the span table, immediately before the
rejected-requests paragraph:

- `lang.detect` is **absent by design** when the request pins `lang=`: the trace's `lang`
  carries the pinned value instead - except on an english-only deployment, where detection
  still runs and records because it gates the422 refusal.
- `route.decide` is **absent by design** when the request pins `model=`: the pin is the
  decision, `route_reason` keeps the router's explicit-model reason, and the decision is
  computed once, untimed.
- `POST /route` accepts no pins, so both spans remain unconditional there.

No other section of the document changed.

## Rollback coupling

`tests/test_real_adapter.py` updates (3 assertions: explicit-model `route.decide` absent,
staged first-checkpoint sync, staged remainder queued) revert together with the group if4.4 is
reverted - plan rollback couples group and tests. No new tests were added (wave3 owns
regression tests for kept changes).

## Observations for Main (reported, NOT applied - catalog is closed, backlog only)

1. The three harness rows above cannot observe engine changes while they boot engineless; if
   a real-engine throughput number is wanted for the keep/revert walk, it needs a harness
   change (backlog, R-11) or the smoke/driver numbers in this file.
2. Startup/prefetch loads run outside any trace, so they emit no `model.load` span; they log
   to the `engine` source instead. Consequence: D-014 `stats.load_ms`/`size_bytes` stay null
   for checkpoints that never load inside a traced request (on-demand `_resident_agent`
   loads still produce spans). A span-less "model event" in the vocabulary would fix it -
   backlog.
3. HF hub prints `Fetching 5 files ...0.00B` during startup cache verification; it is local
   cache inspection, no network bytes, pre-existing laya/hub behavior.

## Cleanup

Throwaway verification scripts (`/tmp/verify44_stub.py`, `/tmp/verify44_real.py`,
`/tmp/verify44_pins.py`), `/tmp/trace44.log`, `/tmp/v44-stub.sqlite3`, smoke state
`/tmp/lw-smoke-510dxogx` and pin state `/tmp/lw-p44` were removed after their outputs were
pasted above. Servers: smoke child stopped by the script itself; pin server hub-stopped; live
`:8050` untouched throughout.
