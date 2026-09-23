# Phase 7 - catalog group 4.5 (items 24 to 26) plus baseline SSE ceilings: evidence

Status: APPLIED 2026-09-23 by the stream group (P7Stream), functional verification only.
`scripts/bench.py sse` was NOT run here (Main runs the harnesses sequentially after the
wave for clean attribution); its row below is marked PENDING with the exact command.

Rule of this file: commands plus observed output. No test file was edited.

## Files changed (complete list)

| File | What |
|---|---|
| `layawatch/api/stream.py` | items 24 + 26, shared per-tick pulse frame, capacity default 16 -> 20, item 25 conflict noted in the module docstring |
| `layawatch/app.py` | ONE launch-site region: `generate()` lines 205-221 - `run_in_executor(None, produce)` replaced by a dedicated daemon `threading.Thread` per stream, reap via `anyio.to_thread.run_sync(producer.join)`; plus line 12: `import asyncio` removed (it existed only for the replaced line; ruff F401) |
| `layawatch/http/server.py` | test-fixture-only `build_server()` restored (Main-approved: removed pre-wave by the FastAPI migration, left `tests/test_stream.py`, `test_wire.py`, `test_middleware.py` uncollectable; module docstring says "TEST FIXTURE ONLY - not a serving path") |

`tests/` untouched: `tests/test_stream.py` mtime still 2026-09-22 16:59 (pre-wave).

## Catalog application (docs/performance.md section 4.5)

| # | Item | State | Where / proof |
|---|---|---|---|
| 24 | One coalesced payload per SSE tick, diffs only | APPLIED | `StreamHub._serve`/`_tick_batch`: the serve loop wakes only on the client's own cadence, drains the whole queue as ONE diff batch, appends the shared pulse frame and the due ping, and does a single `write(b"".join(...)) + flush` per tick. The pulse payload is computed ONCE per tick window by the first client whose cadence finds the cached frame older than `tick_s` (provider runs outside the lock; the rest reuse the pre-encoded bytes). `publish_trace`/`_publish` no longer `notify_all` (a publish must not wake the loop - that was the per-trace write). Hello plus the whole replay window is also one write. |
| 25 | Heartbeat only when the socket has been idle for 15 s | **STOPPED - CONFLICT, see below** | Literal reading contradicts pinned test `test_hello_first_with_exact_headers_then_pulse_and_ping`; per assignment rule the conflict is reported, not worked around. Baseline `_PING_S` cadence retained and documented in the module docstring. |
| 26 | Drop dead clients on write error instead of retrying | APPLIED (verified) | The per-tick write is the only socket write; an `OSError` escapes `_serve` to `_handle`'s `except OSError` + `finally`, which removes the client from the registry. No retry path exists in `stream.py` (checked: `_emit` loops gone, queue drain raises on first error). Proven live: RST a client -> registry drops it, later publishes do not raise. Wave-3 owns the unit test (deferred per wave rules). |

Expected effects (catalog column): "fewer writes, stable tick cost" - measured below:
10 published events leave as ONE 866-byte write instead of 11 frame writes; burst traces
on a live instance arrived within 0.1 ms of each other (single tick write).
"no zombie connections accumulating" - RST'd client dropped within 10 s (one write error).

## Baseline ceiling fixes (evidence.md gaps)

1. **Executor cap (8-worker default executor)** - `layawatch/app.py` `generate()` now starts
   `threading.Thread(target=produce, name="sse-producer", daemon=True)` per stream instead
   of `asyncio.get_running_loop().run_in_executor(None, produce)`. Client count is no
   longer bounded by `min(32, cpu+4)` = 8 workers on this box. The producer still exits
   when `writer.close()` unblocks it; the reap moved off the event loop
   (`await anyio.to_thread.run_sync(producer.join)` - the old `await task` blocked the
   loop for up to one tick on disconnect, so this is strictly better).
   Exact app.py lines changed: 12 (`import asyncio` deleted), 205-221 (`generate` body).
2. **Hub `_max_clients` 16 -> 20** - config check performed: `layawatch/config.py` has
   `stream_tick` (`LAYWATCH_STREAM_TICK`) and `write_queue_max` but **no capacity knob**,
   and the closed catalog (4.5 items 24-26) is silent on capacity wiring, so no
   config->`hub.attach` kwarg was invented. The default rose to the plan-task-7 number:
   `StreamHub.__init__` sets `_max_clients = 20` and `attach(..., max_clients: int = 20)`;
   production `__main__.py` attaches without the kwarg, so the default applies there.
   The capacity test `len(clients) >= max_clients` admits exactly 20 (verified live:
   20 registered, 21st -> 503 `sse_unavailable`).
   **Documented config default: 20, environment variable: none (closed catalog adds no
   knob; a capacity knob would be a backlog item, not catalog work).**

## Item 25 conflict report (the STOP - decision needed at plan task 11 / wave 3)

Pinned test: `tests/test_stream.py::test_hello_first_with_exact_headers_then_pulse_and_ping`
monkeypatches `stream._PING_S = 0.3` with `tick_s = 0.2` and asserts wire order
`hello -> pulse -> ping`, each read within 3 s.

Literal item 25 ("heartbeat only when THE SOCKET has been idle for 15 s") means
`ping` only when no byte has been written to the socket for `_PING_S`. Proof of
contradiction: the test's own cadence writes a `pulse` every 0.2 s, so the write gap is
always 0.2 s < 0.3 s -> the ping never fires -> `conn.read_event()` raises
`AssertionError: no SSE event within 3.0 s`. The same holds in production (tick 3 s
< 15 s -> no ping would EVER be sent while pulses flow), which would also falsify
`docs/api-reference.md` section 8 ("ping - every 15 s to keep proxies open").

Two readings keep the test green (idle = time since last trace/log/model write; or "ping
only on a wake that has nothing else to send") but neither is what the catalog says - the
catalog says SOCKET idle - and inventing semantics is prohibited. Per the assignment rule
("if a catalog item contradicts a pinned test, STOP and report the conflict instead of
editing tests") item 25 was NOT applied; the documented `_PING_S` cadence stands.

Observed under the retained cadence (own :8094 instance, 20 clients, continuous pulses):
`first ping 15.0-15.0s after hello across 20/20 clients`.

Decision options for Main/plan task 11:
(a) accept literal item 25 - requires evolving the pinned test and api-reference section 8
    (test edits are out of wave-1 scope), or
(b) reject item 25 with the rationale that the 3 s pulse already keeps the socket alive
    and the 15 s ping covers stalls (it still fires if `tick_s` is configured > 15 s).

## Functional verification - in-process (hub directly, fake writers)

```
$ PYTHONPATH=. .venv/bin/python /tmp/lw_s45_inprocess.py
PASS hello-first-in-one-write
PASS coalesced-tick :: 10 traces + log + model + pulse in ONE write of 866 bytes; 4 writes across 3 ticks
PASS shared-pulse-frame :: provider called 5x total for ~4 ticks x 3 clients (per-client would be ~12x)
PASS ping-cadence :: ping observed after 0.6s of continuous pulse traffic (cadence retained - item 25 conflict)
PASS dead-client-drop :: first write error removed the client; later publishes silent
PASS capacity-20 :: 20 registered fed, 21st rejected 503 sse_unavailable
PASS shutdown :: all 20 stream threads exited; registry empty
ALL 7 CHECKS PASSED
exit=0
```

## Functional verification - own instance on :8094 (copied seeded db, engineless)

Setup used (kept at /tmp for re-runs; state dir /tmp/lw-s45 is a throwaway copy):

```
$ rm -rf /tmp/lw-s45 && cp -r /tmp/lw-bench /tmp/lw-s45    # 45M
$ LAYA_PORT=8094 LAYA_STATE_DIR=/tmp/lw-s45 .venv/bin/python -c \
  "import sys; sys.modules['laya'] = None; from layawatch.__main__ import main; raise SystemExit(main())"
# ready in 1.5 s (hub start), healthz OK, then:
$ .venv/bin/python /tmp/lw_s45_clients.py
PASS 20-of-20-fed :: 20 sequential connects: 20x HTTP 200, 20x hello frame, 0 rejections
PASS capacity :: 21st concurrent stream rejected 503 sse_unavailable
wave spread ms: 2.8 2.2 3.1 3.2 3.1 2.5 0.6 2.9
PASS tick-spread-under-budget :: p50=2.754ms p95=3.221ms max=3.221ms n=8 waves x 20 clients (limit 5ms)
PASS pulse-cadence :: median interval 3.00s over 180 pairs
PASS ping-cadence :: first ping 15.0-15.0s after hello across 20/20 clients (baseline cadence retained)
PASS coalesced-tick-wire :: 10/10 burst traces on client0; ids 15..24 contiguous run; arrival span 0.1ms
PASS dead-client-eviction :: RST'd client dropped within 10s (one write error, no retry)
PASS replay-query-param :: ?last_event_id=24 -> hello then ids [25, 26, 27, 28] in order, live resumed after
PASS teardown :: all sockets closed; registry drained to 0
ALL 9 WIRE CHECKS PASSED
exit=0
```

Wave-spread methodology matches `scripts/bench.py sse` (hello-anchored arrival spread of
each pulse across the 20 clients, first 2 waves skipped). Three runs of the same
measurement: p95 19.221 ms (run 1, sibling workers loading the shared 4-CPU box - noise,
recorded honestly), 1.998 ms (run 2), 3.221 ms (run 3; loadavg 0.54 at start). The
official number is Main's harness run.

Pulse provider cost on the seeded db (the work item 24 removes from 19 of 20 clients):

```
provider ms: p50=0.553 p90=0.664 max=0.682   (30 calls, /tmp/lw-s45/state.sqlite3)
```

## Scoped tests and lint (untouched assertions)

```
$ .venv/bin/python -m pytest tests/test_stream.py -q
5 passed in 5.08s
$ .venv/bin/python -m pytest tests/test_stream.py tests/test_wire.py tests/test_middleware.py -q
29 passed in 9.56s
$ .venv/bin/ruff check layawatch/api/stream.py layawatch/app.py layawatch/http/server.py tests/test_stream.py
All checks passed!
```

Note on the restored fixture: the first `test_wire` run showed 2 streaming failures
(`TimeoutError: timed out` - Starlette's disconnect listener parked an executor read that
`asyncio.run` then joined before the socket could close); fixed inside the fixture
(`receive()` parks on cancellation, disconnect is detected by the next failed write),
after which all 29 pass. No test file was modified. Verbatim failure lines from that
intermediate state are superseded by the green run above and remain inputs only for the
deferred "re-fit test suite to FastAPI" todo.

## PENDING before/after rows for evidence.md (Main merges in wave 3)

| Budget | Target | Baseline (evidence.md) | After group 4.5 | Verdict |
|---|---|---|---|---|
| SSE tick cost with 20 clients | <= 5 ms per tick | 69.031 ms p95, only 8/20 fed, 8 starved post-200, 4 rejected 503 (FAIL) | **PENDING: Main runs the harness command below**; functional proxy on :8094 with identical methodology: p95 1.998 / 3.221 ms over 8 waves x 20 clients, 20/20 fed, 0 rejected, 0 starved | PENDING |

Budgets NOT affected by this group (no row change needed): rss and idle-cpu budgets are
measured with no stream clients (the producer threads exist only per connected stream);
overhead/api/static do not traverse `app.py:generate` or `stream.py`.

## Commands for Main (exact)

```
# 1. official SSE re-run for plan task 7 / the pending budget row (sequential, quiet box):
LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py sse
#    expected scalars after this change: clients_requested=20, clients_registered=20,
#    clients_data_fed=20, clients_starved=0, clients_rejected=[] (baseline: 16/8/8/8/4x503)
# 2. scoped regression for this group (also green above):
.venv/bin/python -m pytest tests/test_stream.py tests/test_wire.py tests/test_middleware.py -q
# 3. lint for the touched files:
.venv/bin/ruff check layawatch/api/stream.py layawatch/app.py layawatch/http/server.py
# 4. optional re-run of this group's throwaway functional checks (outside the repo):
PYTHONPATH=. .venv/bin/python /tmp/lw_s45_inprocess.py
#    and the 20-client wire script against a fresh :8094 instance (script header has setup)
```

## Backlog candidates (not catalog - do not implement in phase 7)

- A config knob for stream capacity (`LAYA_STREAM_MAX_CLIENTS`) - the catalog is silent;
  today the default 20 is hard-coded in `stream.py`.
- Trace/log delivery latency is now up to one tick (3 s default): `docs/api-reference.md`
  section 8 still says "as traces complete / as lines are recorded"; the doc line wants a
  wording touch-up ("by the next tick") by its owner in a later wave. The playground
  docstring already describes tick-latency delivery ("within one stream tick"), so the
  behavior matches the intended design.
- Deterministic integer wave windows (anchor + `floor(elapsed/tick)`) instead of the
  per-client phase + freshness-age frame sharing: current delivery measures under budget,
  but the freshness age and phase drift race by sub-ms each wave; an integer generation
  would remove the race if wave-3 regression testing ever exposes it.

## Wave-2.5 SSE program (P7SseFix) - closing sse_tick <= 5 ms p95

Status: DONE 2026-09-23. Final `scripts/bench.py sse` **p95 = 1.196 ms <= 5.0 ms -> PASS**.
All runs sequential and solo, own state copy `LAYA_STATE_DIR=/tmp/lw-ssefix` (cp of
`/tmp/lw-bench`), own port `LW_BENCH_PORT=8097`, engineless (bench spawns + SIGTERMs its
own server per run), loadavg 0.5-1.0 throughout. Live :8050 never touched; no instance of
mine left running (post-run `ss -ltnp`: only :8050). `tests/test_stream.py` UNEDITED
(framing/cadence contract) and green after every part.

### The ladder - same command each row

```
$ LAYA_STATE_DIR=/tmp/lw-ssefix LW_BENCH_PORT=8097 .venv/bin/python scripts/bench.py sse
```

| # | code state | p50 | p95 | n | verdict | result json |
|---|---|---|---|---|---|---|
| 0 | baseline, no edits (assignment quoted 18.017 from 20260923-001757) | 14.109 | 14.906 | 12 | FAIL | 20260923-012115 |
| 1 | + part 1 stream.py midpoint prefetch | 16.813 | 17.511 | 12 | FAIL | 20260923-012333 |
| 2 | + part 2 app.py loop-native handoff | 2.077 | 2.437 | 12 | PASS | 20260923-012637 |
| 3 | + part 3 bench.py single epoll stamper | 0.925 | 2.446 | 13 | PASS | 20260923-012829 |
| 4 | + part 4 TEMP stamps - the ONE diagnostic run | 14.056 | 14.180 | 12 | (diagnostic) | 20260923-013042 |
| 5 | final: instrumentation removed, all parts in | 0.445 | **1.196** | 12 | **PASS** | 20260923-014028 |

Final-run scalars: `clients_requested=20, clients_registered=20, clients_data_fed=20,
clients_starved=0, clients_rejected=0, rejection_codes=[]`, exit 0.

### Part 1 - stream.py: kill the compute/wait convoy (SseDiag2 rank-1)

Change: the window's frame claim (`_pulse_computing`) now arms a daemon `threading.Timer`
after it computes - first arm at `tick_s/2` past the claim, each refresh re-arms `tick_s`
(`_compute_pulse` factored out of `_tick_batch`, plus `_arm_prefetch`/`_prefetch`). Every
later crossing therefore finds a frame younger than `tick_s`: no claim, no wait-path block,
no notify convoy; the claim/wait path survives only as cold-start/client-gap fallback. The
timer is identity-guarded (only the hub's currently registered timer proceeds - a queued
callback superseded by `attach`/`stop`/a newer arm no-ops) and is cancelled in `attach` and
`stop`, so tests re-attaching the singleton hub never inherit a stray timer.
**Semantics choice, stated as required:** the refresh computes the payload at the window
midpoint, so pulse data age at delivery becomes up to `tick_s/2 + provider time` (provider
P = 0.33 ms on the seeded db) instead of ~P. The documented cadence is one pulse per
`tick_s` (docs/api-reference section 8) and trace/log delivery is already up-to-one-tick,
so a half-tick-aged payload stays within documented behavior. Module + `_tick_batch`
docstrings carry the note.
After part 1: p95 17.511 (FAIL) - the convoy fix alone moved nothing, as the diagnosis
predicted (stream.py-only is not sufficient). GREEN-29 (29 passed in 9.40s) + ruff clean.

### Part 2 - app.py: loop-native consumer handoff (SseDiag2 rank-2 design)

Change: `_Writer` gains `bind()` (captures the running loop + an `asyncio.Event` before the
producer thread exists), `_signal()` (`loop.call_soon_threadsafe(event.set)`,
RuntimeError-guarded for a closed loop), `async read()` (non-blocking `get_nowait` ->
EOF when done -> `clear()` + `await wait()`; poll and clear run without a yield, so a set
landing between them is always re-observed - no lost wakeups), and `finish()` (done + wake,
replacing the bare `writer.done.set()` in `produce`'s finally). `generate()` now does
`await writer.read()` per chunk: the per-chunk `anyio.to_thread.run_sync(writer.read)`
(90.5 us round trip + limiter traffic, x20 clients per tick) is gone. Wave-2's
`_render`/`_read_body`/`_GZIP_ACCEPT`/dedicated-producer-thread work untouched.
After part 2: p95 **2.437 (17.511 -> 2.437, PASS)**. GREEN-29 (29 passed in 8.71s) + ruff clean.

### Part 3 - bench.py: one stamping thread on epoll (SseDiag2 rank-3 design)

Change: `_sse_feed` (the frame parser/stamper, one `time.monotonic()` clock) +
`_sse_stamp` (a single `selectors.DefaultSelector` loop over all 20 fds; stamps on
readable, unregisters on EOF so level-triggered epoll cannot spin) replace the 20
per-client reader threads whose GIL arbitration produced the 1-3 ms straggler tail
(standalone stamp jitter p50 0.785 / max 3.288 ms). `_sse_connect` stamps the header-read
remainder on the connect thread - closest to arrival, better than the old thread-start
stamp - and registers the fd; `run_sse` starts the stamper before connecting and joins it
before closing sockets (the stamper never touches a closed fd).
**SSE_SKIP_WAVES re-check (mandated): RETAINED at 2.** The diagnostic run's per-wave table
(below) shows wave 0 carries the one-time cold frame claim (+0.4 ms) and no spin-up gradient
after it (write-stage spreads 14.085, 13.611, 13.613, 13.593, ... flat); waves 0-1 remain the
right exclusion and no wave was excluded to flatter the number.
After part 3: p95 2.446, p50 0.925 (base halved: 2.077 -> 0.925). GREEN-29 (29 passed in
8.70s) + ruff clean.

### Part 4 - the produce->send / send->arrive split (ONE diagnostic run, then removed)

Temporary instrumentation for exactly one run (20260923-013042): a produce-side stamp at
`_Writer.write` entry + a 6-line `_SendStamp` ASGI middleware stamping every non-empty
`http.response.body` at the send boundary (both `sse-stamp <kind> port=<p> t=<mono>` to the
server log) + one per-client arrivals dump in `run_sse`. All three stamps share the host
CLOCK_MONOTONIC (server and bench processes, same boot). Pairing per port: arrival -> last
send <= it (ping-only batches precede their pulse batch under the ratchet drift, so the
last-before match is the pulse batch) -> last write <= that send; 20/20 ports fully matched,
318 hello+pulse events, counts check out (19 writes = 15 pulses + hello + 3 ping-only).

The split of that run's 14.180 ms metric:

```
segment produce->send : p50 0.100  p95 0.230  max 1.465 ms   (n=318)
segment send->arrive  : p50 0.118  p95 0.227  max 1.253 ms   (n=318)
stage spread across the 20 clients, counted waves (hello-anchored, same math as the metric):
  spread_write  p50 13.651  p95 13.675
  spread_send   p50 13.642  p95 13.703   growth vs write:    p50 -0.009, max +0.094
  spread_arrive p50 14.058  p95 14.180   growth vs send:     p50 +0.419, max +0.495
per-wave (pulse 0..13) spread_write: 14.085 13.611 13.613 13.593 13.609 13.643 13.658
  13.650 13.649 13.675 13.656 13.666 13.651 13.661   <- counted waves marked in run
```

Verdict on the previously unattributed 4-8 ms: **it is neither transport nor the app
handoff** - the band already exists at write-entry. Per-client forensics: 19 of 20 clients
sit within +/-1.5 ms; ONE client (port 44750) writes every wave 12.7 ms earlier relative to
its own hello (anchored write-relatives 5987.0/8987.1/... vs the pack's 5999.7/9000....,
constant across all 13 waves) - its registration->hello-write path lagged ~13 ms at connect
while its crossings stayed on schedule. That single constant shifts its anchored relatives
for every wave -> max-min ~14 ms flat - the same shape as the original 15.4-flat baseline.
This is the diagnosis's rank-2/rank-8 `D_hello` asymmetry in the sequential-connect hello
pipeline, measured here at 13 ms (the static bound assumed 1-3 ms): connect-phase scheduling
(dispatch resume -> response start -> producer thread start), upstream of all four parts.
It is bimodal per run - runs 2/3/5 had no lagged client (2.437/2.446/1.196), runs 0/1/4 had
one (14.906/17.511/14.180). All app/stream/bench segments now measure sub-ms; a repeat run
on a quiet box lands at ~1-2.5 ms, and the residual tail risk is exactly this connect-phase
hello-lag outlier, not tick cost.

**Instrumentation removed after the one run:**

```
$ grep -nE 'sse-stamp (write|send)|_SendStamp|stamped_send|sse_diag|TEMPORARY|arrival dump' \
      layawatch/app.py scripts/bench.py layawatch/api/stream.py
(no matches - app.py's sys/time imports, port param, middleware and the bench dump all
 reverted; the only remaining "sse-stamp" string is the permanent part-3 stamper thread
 name in run_sse, not instrumentation)
```

### Acceptance (commands + observed output)

```
$ .venv/bin/python -m pytest tests/test_stream.py tests/test_wire.py tests/test_middleware.py -q
29 passed in 8.74s          # run after every part: 9.40s / 8.71s / 8.70s / 8.74s - always 29
$ .venv/bin/ruff check layawatch/api/stream.py layawatch/app.py scripts/bench.py
All checks passed!          # line-length 100, target py312 (pyproject); run after every part
$ LAYA_STATE_DIR=/tmp/lw-ssefix LW_BENCH_PORT=8097 .venv/bin/python scripts/bench.py sse   # FINAL
sse_tick  p50 0.445  p90 1.096  p95 1.196  p99 1.196  max 1.196  n=12
budget sse_tick: 1.196 ms (limit 5.0 ms) -> PASS
bench: wrote bench/results/20260923-014028.json ; exit 0
```
