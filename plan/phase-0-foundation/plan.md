# Phase 0 - Foundation and scaffolding

Status: planned
Depends on: nothing
Estimated effort: 2 to 3 days
Deliverable: a repository that starts, serves, migrates and tests, with the design tokens already
ported from the approved mock.

## Objective

Turn the current single-file deployment (`serve.py`, `admin.html`, `smoke_test.py`) into the LayaWatch
repository skeleton: package layout, config, storage with migrations, HTTP server, static serving,
design tokens, dev tooling and CI. No observability features yet.

## Scope

In: repo layout, Python package skeleton, config validation, SQLite schema and migration runner,
HTTP server with routing and static file serving, token stylesheet ported from the mock, test harness
with a fake engine adapter, Makefile, CI workflow, moving the mock and legacy files into place.

Out: tracing, storage writer, API resources beyond health and meta, auth, any Next.js code.

## Deliverables

```
pyproject.toml                 metadata, ruff and pytest config, no runtime deps
Makefile                       dev, test, lint, web-build, smoke, migrate targets
layawatch/__main__.py          entrypoint, --migrate-only flag
layawatch/config.py            env parsing, defaults, validation
layawatch/log.py               ring-buffer log sink plus stderr lines
layawatch/http/server.py       ThreadingHTTPServer, graceful shutdown, timeouts
layawatch/http/router.py       path to handler table, 404/405, error envelope
layawatch/http/static.py       web/out serving, ETag, cache headers, route resolution
layawatch/store/db.py          WAL connection factory, busy timeout, pragmas
layawatch/store/schema.sql     full DDL (architecture.md section 5)
layawatch/store/migrations/    migrations/0001_init.sql plus runner
layawatch/engine/adapter.py    interface plus fake adapter used by tests
web/design/mock.html           moved from the repo root
legacy/serve.py                snapshot of the pre-LayaWatch host, reference only
legacy/admin.html              snapshot of the pre-LayaWatch admin page
scripts/smoke_laya.py          moved from smoke_test.py
tests/                         config, migrations, static, router tests
.github/workflows/ci.yml       lint, tests, web build (web job stubbed until Phase 4)
```

## Tasks

1. Create the layout above; move `dashboard-mock.html` to `web/design/mock.html`, `serve.py` and
   `admin.html` to `legacy/`, `smoke_test.py` to `scripts/smoke_laya.py` (adjusting its import path
   comments only). Delete `__pycache__`.
2. Write `pyproject.toml` (project metadata, `[tool.ruff]` line length 100, `[tool.pytest.ini_options]`
   `testpaths = tests`), `requirements-dev.txt` (pytest, ruff, playwright), and the `Makefile` targets.
3. Implement `config.py`: parse every variable in `docs/architecture.md` section 6, typed accessors,
   fail-fast validation with a single aggregated error message.
4. Implement `log.py`: level filter, ring sink (for Phase 1), stderr format
   `YYYY-MM-DD HH:MM:SS source message`.
5. Implement `store/db.py` and the migration runner: apply `migrations/*.sql` in filename order inside
   a transaction, record in `schema_migrations`, refuse to start when the database version is newer
   than the code. `--migrate-only` applies and exits.
6. Implement `http/server.py` and `http/router.py`: routing table, JSON helpers, error envelope,
   `X-Request-Id` generation, body size limit, socket timeout, `SIGTERM` graceful shutdown.
7. Implement `http/static.py`: serve `web/out` when present, ETag and `If-None-Match`, immutable cache
   for `/_next/static/*`, `no-cache` for HTML, extension allowlist, path traversal rejection, 404 page,
   and the build-instructions page when `web/out` is absent.
8. Port the approved mock's tokens into `web/src/styles/tokens.css`: both themes, every role from
   `docs/design-language.md` section 1, plus a `web/design/tokens-preview.html` page that renders
   swatches and component states for visual review without the Next.js app.
9. Define `engine/adapter.py`: `predict`, `route`, `loaded`, `load`, `unload`, `device`, plus
   `FakeAdapter` with configurable latency and error injection.
10. Add tests: config validation matrix, migration idempotency and version guard, router 404/405,
    static ETag and traversal rejection, error envelope shape, fake adapter contract.
11. Add `.github/workflows/ci.yml`: ruff, pytest, and a placeholder web job gated on Phase 4.

## Acceptance criteria

1. `make dev` starts the process; `GET /healthz` returns `{"status":"ok", ...}` with the fake adapter.
2. `GET /` returns the build-instructions page when `web/out` is absent and the UI when present.
3. `python -m layawatch --migrate-only` twice in a row is a no-op; a database with a higher
   `schema_migrations` version refuses to start with a clear message.
4. `make test` passes with no engine, no torch import and no network.
5. `web/design/tokens-preview.html` renders both themes side by side and matches the mock's palette
   for every role.
6. `git remote -v` shows `origin https://github.com/BLANCO-11/LayaWatch.git`; the repository has an
   initial commit with no state files, no model cache and no `__pycache__`.

## Evidence required

- `make test` output (pass counts).
- `curl -s localhost:8050/healthz` and `curl -sI localhost:8050/` headers showing cache policy.
- Migration log lines for a fresh database and for a re-run.
- Screenshot of `tokens-preview.html` in dark and light themes.
- `du -sh .venv` style statement of what the repo does and does not contain (no model cache).

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Scope creep into Phase 1 features | the deliverables list is closed; anything else goes to the Phase 1 plan |
| Static route resolution bugs (export layouts differ from assumptions) | test with a hand-written fixture tree before the real export exists |
| Config sprawl | one table in architecture.md is the only source; a test asserts every documented variable is parsed |

## Rollback

Phase 0 adds files only; reverting is `git revert` of the scaffolding commit. The legacy snapshot in
`legacy/` keeps the original host runnable during the transition.

## Exit gate

All six acceptance criteria verified, evidence recorded in `evidence.md`, decisions recorded for the
server choice, migration strategy and token port.
