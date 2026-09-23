# Phase 7 evidence - catalog group 4.6 (items 27-31), plan task 8 - Frontend

Status: functional verification complete on Frontend port 8095 against a copy of the seeded
database (`/tmp/lw-fe46/state`); budgets re-measured PENDING Main's sequential post-wave runs.
Regenerated `web/out` is live on :8050 (intended).

## Files changed (Frontend group, wave 1)

| File | Change |
|---|---|
| `web/scripts/precompress.mjs` | NEW: build-time `.br`/`.gz` sidecar generator (item 31) |
| `web/package.json` | `"postbuild": "node scripts/precompress.mjs"` wiring |
| `layawatch/http/static.py` | Accept-Encoding negotiation, sidecar serving, Vary, variant ETag/304 |
| `web/src/components/ui/table.css` | `content-visibility: auto` on >100-row tables (item 30) |
| `scripts/web_check.py` | bundle ceiling excludes (and reports) derived sidecar bytes |

No other files touched. No changes to `web/src/lib/stream.ts` (item 29 pre-existing, confirmed below).

## Item-by-item (catalog 4.6)

### 27 - Static export, no chart library, hand-built SVG - CONFIRMED pre-existing
- `web/package.json`: zero runtime dependencies (devDeps only: next, react, react-dom, typescript, @types/*).
- `web/package-lock.json` package-id audit for `chart|d3|plotly|echart|apex|highcharts|victory|nivo|visx` -> no hits.
- Bundle scan: all 26 exported JS files searched for 11 chart-library markers -> 0 hits.
- Charts are local SVG components (`web/src/components/charts/*`, catalog families chart/area-chart/
  line-chart/stacked-bar/axis/legend/crosshair/waterfall present - `web_check.py catalog` gate green).
- Static export confirmed: `next.config.mjs output: "export"`, build writes `web/out`, server serves
  it with no Node runtime involved.

### 28 - Route-level code splitting - CONFIRMED (build output + runtime)
- `npm run build` route table: 16 routes exported (/, /audit, /checkpoints, /dev/catalog, /keys,
  /login, /logs, /metrics, /playground, /settings, /setup, /traces, /traces/[id]->/traces/detail,
  /users, /_not-found, 404).
- Chunk analysis of `web/out`: 22 distinct JS chunks = 8 shared (referenced by multiple routes) +
  14 route-specific. Each route HTML references exactly 8 shared + 1 own chunk (404: shared only),
  so first load never carries another route's code.
- Runtime (headless browser on :8095): client-side nav `/traces` -> `/traces/5c0bcfe8` fetched the
  detail-only chunk `_next/static/chunks/0pcxgid1d_34v.js` on demand (absent from first load);
  `/playground` chunk `0t_cxfj__y0k7.js` also fetched via sidebar prefetch + nav. Observation:
  Next prefetches visible sidebar links, so some route chunks warm during idle - still not in the
  initial document's references.

### 29 - SSE with polling fallback only - CONFIRMED pre-existing (P5), runtime-proven
- `web/src/lib/stream.ts`: `onPoll` path fires `metrics/summary` + `traces` + `logs` after
  `FALLBACK_AFTER_FAILURES = 2`, stops on reconnect (`stopPolling` in `onopen`); registered by
  home, `/logs`, `/traces` pages.
- Runtime proof (browser request interception aborting `/api/v1/stream`):
  - run 1: 8 EventSource attempts aborted -> 10 poll requests fired (the exact trio above);
    on release pill returned to `live`.
  - run 2: pill samples during outage = `polling 3s`, `polling 3s`, `reconnecting`;
    0 poll requests after release; pill `live`.
  - polling therefore happens only while the stream is down and ceases on recovery.
- Observation (no catalog item, no change made): while retries continue during a sustained outage
  the label alternates `polling 3s` / `reconnecting` because each retry sets `reconnecting`; the
  polling requests themselves keep firing. Note for backlog review if desired.

### 30 - Skeletons + content-visibility + log virtualization
- Skeletons: CONFIRMED pre-existing - `Skeleton` renders the 6-row table loading state
  (`Table.tsx`), used on home, metrics, traces detail, audit, logs, settings, users, catalog.
- Log virtualization >500 lines: CONFIRMED pre-existing (`VIRTUALIZE_AT = 500` in `LogViewer.tsx`),
  runtime-proven on seeded data: 600 lines loaded -> hint "600 lines loaded, windowed view",
  only 81 `.lw-log-line` DOM nodes + spacer pad.
- **APPLIED**: `table.css` now sets `content-visibility: auto; contain-intrinsic-size: auto 45px`
  on `tbody` rows of tables with >100 rows (`:has(tbody tr:nth-child(101))` guard keeps short
  tables and the 6-row skeleton untouched), plus `@media print { content-visibility: visible }`
  so print keeps full rows. Row height estimate 45px matches measurement.
- Runtime proof on `/traces` with 150 rows (50 + 2x Load older): computed
  `content-visibility: auto`, `contain-intrinsic-size: auto 45px`, all sampled row heights 45px,
  0 zero-height rows, horizontal overflow 0, 7-column header intact; screenshot clean
  (150 rows, uniform, no gaps). Shipped CSS carries it (`web/out/_next/static/chunks/*.css`
  contains `content-visibility`).

### 31 - Precompressed .br/.gz served directly - APPLIED
- Build step: `npm run build` -> `next build` + `postbuild` runs `web/scripts/precompress.mjs`:
  every >=1 KiB `.html/.css/.js/.mjs/.json/.map/.svg/.txt` in `web/out` gets `.br` (brotli q11)
  and `.gz` (gzip L9) sidecars when they shrink the file. Final build:
  `precompress:93 files (1391190 B) ->93 .br +93 .gz sidecars,712660 B on disk`, build EXIT=0.
- Serving (`layawatch/http/static.py`): negotiates `br` then `gzip` from Accept-Encoding
  (q=0 excluded), sends `Content-Encoding` + `Vary: Accept-Encoding` whenever any sidecar exists
  (identity responses vary too), ETag computed per variant, sidecar path containment-checked
  (symlink escape -> identity fallback), sidecars not directly fetchable (`br`/`gz` not in the
  extension allowlist), unreadable sidecar falls back to identity. `app.py` runtime gzip only
  touches JSON and skips when `Content-Encoding` is set -> no double compression.
- PRESERVED: deep-link fallback (`/traces/<id>` -> exemplar) and ETag/304 flow untouched; the
  `api/health.py` 2-arg `resolve()` caller still works (accept defaults to `""`).

## Bundle size report (`scripts/web_check.py bundle`, exit0)

```
bundle:26 JS files, worst gzip71459 B (_next/static/chunks/0bma92pht_c97.js)
bundle: total export1403691 B (ceiling3145728 B); precompressed sidecars712660 B
         in186 files (excluded from ceiling)
bundle: ceilings hold
```

| Artifact | Raw B | gzip B |
|---|---:|---:|
| chunk 0bma92pht_c97.js (framework) | 229156 | 71768 |
| chunk 2_2-1c0c4snqt.js | 159349 | 43791 |
| chunk 0cz1d0mv5g_q7.js | 112594 | 39494 |
| chunk 0_nowz57oa-ro.js | 41333 | 11498 |
| chunk 0-fgc7lu98d9c.js | 30994 | 9255 |
| chunk 0j49yl0b-moab.js | 25391 | 9213 |
| all26 JS | ~956 k | 259718 |
| worst HTML (login.html) | ~12 k |2390 |
| export payload total |1403691 | - |
| sidecars (.br+.gz,186 files) |712660 | - |

Per-route first load:8 shared chunks +1 route chunk each (see item 28).

## Budget rows affected - PENDING BEFORE/AFTER (Main runs post-wave, sequentially)

| Budget | Baseline (evidence.md) | After group4.6 | Status |
|---|---|---|---|
| Static asset response <=5 ms p95,304 when unchanged |2.751 ms p95;100/100304 (`bench.py static`, n=100) | bench client sends `Accept-Encoding: gzip`, so it now receives the `.gz` sidecar path instead of the raw body; expect <=5 ms p95 and100/100304 | **PENDING** Main: `scripts/bench.py static` |
| SSE tick cost with20 clients <=5 ms |69.031 ms p95 FAIL (baseline stream gaps, server-side) | frontend group touches no server tick code; re-run for the wave record | **PENDING** Main: `scripts/bench.py sse` |

## Commands Main should run (from repo root, sequential, not in this wave)

```bash
# budgets for this group
LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py static
LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py sse
# bundle/gates (also green during this wave)
.venv/bin/python scripts/web_check.py catalog bundle a11y
```

## Verification log (this wave, observed)

- `cd web && npm run build` -> EXIT=0 (twice; second run is the final state), postbuild ran both times.
- `scripts/web_check.py catalog bundle a11y` -> EXIT=0 (after final build).
- `ruff check --select E4,E7,E9,F,I layawatch/http/static.py scripts/web_check.py` -> clean.
- `pytest -q /tmp/lw-fe46/test_precompress.py tests/test_static.py` ->24 passed (9 new throwaway
  proofs: br preferred, gzip-only, q=0, identity+Vary, no-sidecar-no-Vary, per-variant ETag/304,
  deep-link200 plain+encoded, traversal/direct-sidecar/symlink-escape;15 existing static tests
  unchanged and green).
- Live HTTP against :8095: `Accept-Encoding: br` -> `content-encoding: br, vary: Accept-Encoding,
  cache-control: ...immutable, content-length8068` (raw30994); gzip etag -> conditional ->304
  with ETag+Vary; identity ->200 +Vary, no Content-Encoding; `/traces/does-not-exist-xyz` ->
200 exemplar with gzip; `/../state.sqlite3` ->404; `.../chunk.js.br` direct ->404.
  br and gz bodies decompress byte-identical to the source file (node zlib round-trip).
- Browser (:8095): login -> pill `live`; traces150 rows content-visibility active (screenshot);
  log viewer600 lines windowed (81 DOM nodes); stream abort -> `polling3s` + poll trio ->
  release -> `live`,0 further polls; on-demand detail chunk fetch; all132 requests same-origin
  (no third-party/chart hosts).
