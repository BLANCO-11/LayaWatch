# Phase 0 outcome

Status: complete (2026-09-22). Gate: all six acceptance criteria verified; see `evidence.md`.

## What shipped

- Package skeleton `layawatch/`: `config.py` (full section 6 table, aggregated fail-fast errors),
  `log.py` (level filter + 2000-entry ring), `http/types.py` (Request/Response/error envelope),
  `http/router.py` (exact and prefix patterns, HEAD/OPTIONS/405, envelope rendering, 8-hex
  `X-Request-Id` on every response), `http/server.py` (threading server, 411/413 body framing,
  graceful SIGTERM, `Server: LayaWatch` with no Python banner), `http/static.py` (ETag/304,
  immutable `/_next/static`, traversal rejection, extension allowlist, build page and HTML 404
  when `web/out` is absent, JSON envelope on `/api/*`), `store/db.py` + `schema.sql` +
  `migrations/0001_init.sql` (11 tables, WAL, idempotent runner, `SchemaTooNewError`),
  `engine/adapter.py` (`EngineAdapter` Protocol + deterministic thread-safe `FakeAdapter`),
  `api/health.py`, `__main__.py` (`--migrate-only`).
- Design foundation: `web/src/styles/tokens.css` (both themes, every role of design-language
  section 1, mock-identical values) and `web/design/tokens-preview.html` (side-by-side review page;
  screenshot in `evidence.md`).
- Tooling: `pyproject.toml`, `requirements-dev.txt`, `Makefile` (bootstrap, dev, migrate, test,
  lint, web-build, smoke, all), `.github/workflows/ci.yml` (ruff, pytest, web placeholder job).
- Tests: 107 passing, ruff clean. Task 1 of the plan (file moves, legacy snapshot, `__pycache__`
  removal) and task 2's git baseline were completed before the phase started, during planning.

## What did not ship (by scope)

- Tracing, storage writer, rings feeding SSE (Phase 1); read API beyond healthz (Phase 2); auth
  (Phase 3); any Next.js code or `web/out` export (Phase 4); the real laya adapter (Phase 1
  provides `RealAdapter`, this phase ships only the interface and the fake).

## What Phase 1 inherits

- Router/server hooks where spans belong (`http.receive` at request start, `response.send` before
  write) and the writer's connection pattern (`store/db.connect` is ready; handlers must enqueue,
  not write).
- `FakeAdapter` as the contract every real adapter must satisfy, plus `counters()` for assertions.
- Config knobs `log_level`/`log_ring_size` already feed `log.configure`; the ring is what the SSE
  `log` events will read (`ring_entries`).
- Open issue P0-I1 (two config rows missing from docs section 6) for the next docs pass.

## Follow-ups

- Evidence commands and outputs are transcribed in `evidence.md`; the screenshot
  `tokens-preview.png` lives beside it.
