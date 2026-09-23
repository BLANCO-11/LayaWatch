# Phase 7 - catalog group 4.2 (recorder, docs/performance.md section 4 items 6-10) - evidence

Status: RECORDER GROUP APPLIED 2026-09-23 (wave 2, phase A). Files changed (4.2 scope only):
`layawatch/obs/recorder.py` and `layawatch/log.py`. No test file was edited; no catalog-vs-pinned-
test conflict was found, so nothing STOPPED. All numbers below are from my own harness runs on my
own state-dir copy `/tmp/lw-p7w2` (`cp -r /tmp/lw-bench`, 46M), bench children engineless on
`LW_BENCH_PORT=8096`; the live `:8050` hub instance was never touched and no instance of mine is
left running.

## 1. Item-by-item: change -> verification

| # | Item | Change | Verification (this file) |
|---|---|---|---|
| 6 | `__slots__` on `Trace` and `Span`; one attribute dict per span | `@dataclass(slots=True)` on `Trace` and on `Observation` - `Observation` is the span record (nine allocated per request; `type` span/generation/event), `Trace` the finished request. The `_Span` context manager already carried `__slots__` at baseline. The "one attribute dict per span built from the vocabulary schema" sub-clause already held: `Context.span` builds exactly one dict via `vocabulary.validate(name, attrs)`. No dynamic-attribute user exists (grep `setattr\|dataclasses.replace\|__dict__` over `layawatch/` = no matches), so slots cannot break a caller. | `tracemalloc` snapshot (catalog verify column): retained population of 9000 spans + 1000 traces = pre-4.2 shape (no slots) **1847040 B** vs slots **1669496 B**, saved **177544 B (9.6 %)** ~197 B/request; per-record residency 344 B -> 128 B (spans). Probe command in section 3. |
| 7 | Two `time.perf_counter()` calls per span, no `datetime` | NO CODE CHANGE NEEDED - already applied at baseline: `_Span.__enter__` and `__exit__` each take one `perf_counter` (2 per span), `Context.event` takes one (instant, duration 0), `Context.__init__` takes `perf_counter` (trace origin) plus one `time.time()` per TRACE for the wall-clock `ts_start` column, and `recorder.py` imports no `datetime`. | Code review + probe: instrumented `time.perf_counter` during `start` -> 1 span -> `finish` counted **exactly 4 calls** (start 1 + enter 1 + exit 1 + finish 1 = 2 per span); source contains no `datetime` token. Overhead bench below. |
| 8 | Fixed-size `deque` rings, no copying when handing data to SSE (index snapshot) | Both rings were already fixed-size `deque(maxlen=...)`. Rewrote the reads: `Recorder.ring_traces(n)` and `log.ring_entries(n)` now take an **index snapshot under the lock** and copy only the newest `n` slots (`[d[i] for i in range(length-n, length)]`) instead of `list(d)` (full ring) plus a second slice copy. Bounded hand-off: O(n) not O(ring). | Semantics probe: `n=None`/`n<0` full snapshot, `n=0` -> `[]`, `n>=len` -> all, `n` -> newest n oldest-to-newest, both rings; matches the pinned `test_ring_evicts_fifo_at_ring_size` expectations. Tests in section 4. Reconnect replay readers still get list semantics (index snapshot == element order). |
| 9 | Skip log-line formatting entirely when level is filtered | REPORT - ALREADY APPLIED in `log.py`: `_emit` builds/writes the stderr line (`time.localtime` + `time.strftime` + f-string + `write`) only inside `if value >= minimum:`; for a filtered line the only remaining work is the ring record + sink offer, which the module docstring pins as "always records into the ring" (dropping those would weaken a documented guarantee - not done). | Runtime probe: at `configure("info")` a `debug("hidden-fmt-check")` emits nothing to captured stderr; the next `info` writes `... s shown-line`. Scoped tests green (section 4). NOTE (out of 4.2 file scope, flagged, NOT edited): caller-side eager debug f-strings exist at `http/server.py:64` (access log, per request) and `engine/adapter.py:853/865` (prefetch); server.py belongs to phase B's file list but item 9 binds to log.py - backlog for wave 3 unless Main re-scopes. |
| 10 | Sampling decision once per trace, before span assembly | `Context.finish` now takes the single RNG draw and computes `always_keep`/`keep` **immediately after `duration`, before any per-observation work**. Kept path unchanged (leaked-span close, top-level sum, framework clamp, attach observations). Sampled-out path is now cheap: no observation loops at all, empty `observations`, ring skip, drop notice, `on_trace` delivery. One draw per trace keeps the seeded sequence identical (draw position within `finish` does not reorder draws across traces). | Tests `test_sampling_rate_zero_keeps_errors_and_delivers_success_without_detail`, `test_sampling_is_deterministic_with_a_seeded_rng`, `test_playground_runs_are_always_recorded` pass unedited (section 4). One deliberate semantics note: for a sampled-out trace `meta["framework_ms"]` is now `duration_ms` (totals rule over the trace's OWN attached spans = none) instead of `duration - sum(detached spans)`; the old value was unreconstructible from the delivered trace and is **never persisted** (sampled-out writes no trace row - writer docstring/test) and **never fanned to SSE** (`on_flush` receives kept traces only, `__main__._publish_flushed`), so no observable consumer changes. No pinned test asserts it. |

## 2. BEFORE/AFTER - `scripts/bench.py overhead` (the assignment's required measurement)

```
$ LAYA_STATE_DIR=/tmp/lw-p7w2 LW_BENCH_PORT=8096 .venv/bin/python scripts/bench.py overhead
```

| Run | record_on p95 | record_off p95 | overhead_added p95 | budget |
|---|---|---|---|---|
| BEFORE (post-wave-1, my given before-table) | - | - | **-0.451 ms** | PASS (<= 3.0 ms) |
| AFTER 4.2, run 1 | 2.397 | 1.905 | **+0.492 ms** | PASS |
| AFTER 4.2, run 2 (variance probe) | 1.538 | 1.562 | **-0.024 ms** | PASS |
| AFTER 4.2, run 3 (variance probe) | 1.570 | 1.398 | **+0.172 ms** | PASS |

All three AFTER runs pass the 3 ms budget. The p95-of-differences series swings ~0.5 ms between
consecutive runs on this box (run 1's record_on p95 2.397 vs runs 2-3 at ~1.54 with equal
record_off), i.e. the run-to-run spread (~+/-0.5 ms) is larger than any delta versus the given
before (-0.451): **no measurable regression and no measurable win at the harness level; the
recorder change's measured effect is within noise.** Absolute record_on p95 after: 1.54-2.40 ms.
Machine: same as baseline (4 cpus, quiet). Raw JSONs: `bench/results/20260923-003544.json` + 2 more.

## 3. Commands + observed outputs (verification trail)

Item 6 tracemalloc snapshot (throwaway probe, mirror dataclass = same fields minus slots):

```
pre-4.2 shape (no slots): retained 9000 spans + 1000 traces -> tracemalloc peak 1847040 B
4.2 item 6   (slots)     : retained 9000 spans + 1000 traces -> tracemalloc peak 1669496 B
saved = 177544 B (9.6%) over one bench-shaped population
```

Item 7/9/10 probe (throwaway, output):

```
SMOKE A: slots present, dynamic attr raises AttributeError; validate builds exactly 1 dict;
sampled-out: sampled=False, observations==[], framework_ms==duration_ms, no ring, 1 drop notice;
kept: framework_ms == approx(duration - top-level sum), sampled=True; rate-0 refusals stay kept;
ring_traces/ring_entries index-snapshot semantics == pinned contract;
filtered debug emits nothing to stderr, next info writes "s shown-line";
instrumented perf_counter over start+1 span+finish = 4 calls (2 per span).
```

## 4. Scoped suites + lint + GREEN-29 (after all Phase A edits)

```
$ .venv/bin/python -m pytest tests/test_recorder.py tests/test_rollup.py \
      tests/test_redact.py tests/test_vocabulary.py -q
53 passed in 0.18s

$ .venv/bin/python -m pytest tests/test_stream.py tests/test_wire.py tests/test_middleware.py -q
29 passed in 9.60s          # GREEN-29 preserved

$ .venv/bin/ruff check layawatch/obs/recorder.py layawatch/log.py
All checks passed!
```

No test was modified; no catalog-vs-pinned-test conflict to STOP on (catalog items 6/8/10 are
covered by existing pins exactly as written; item 7 needed no change; item 9 already held).

## 5. Revert-coupling notes (for wave 3 keep/revert)

- Item 6 (slots): self-contained on the two dataclasses; reverting = drop `slots=True`. Couples to
  any future code that would set undeclared attributes on `Trace`/`Observation` - none exists today.
- Item 8 (index snapshot): reader semantics pinned by `test_ring_evicts_fifo_at_ring_size` +
  `test_wire`'s `ring_entries()` consumers; revert restores the double full-copy, behavior identical.
- Item 9: no code changed - nothing to revert; the caller-side eager-format flag (server.py:64)
  is a backlog item scoped OUT of this group.
- Item 10 (decision-first): confined to `Context.finish`. Revert = move the draw block back below
  the stack loop; seeded RNG order and all sampling pins are unaffected either way. Its only
  behavior delta (`framework_ms` on sampled-out traces, section 1 item 10) reverts with it and has
  no persisted/SSE consumer.
- Group coupling to 4.1: recorder changes sit under the same `bench.py overhead` A/B that phase B
  re-runs; phase B's BEFORE row for overhead is this section's AFTER numbers (+0.492 / -0.024 /
  +0.172; wave-1 end -0.451), so any phase-B delta is read against both.
