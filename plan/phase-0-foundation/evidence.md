# Phase 0 evidence

Status: complete. Every command below was run from the repository root on 2026-09-22 and its
output is transcribed verbatim (elisions marked).

## Environment

| Command | Observed |
|---|---|
| `.venv/bin/python --version` | Python 3.12.14 (uv-managed CPython, venv created for this phase) |
| `.venv/bin/python -m pytest --version` | pytest 9.1.1 |
| `.venv/bin/ruff --version` | ruff 0.16.8 |
| `make --version` | GNU Make 4.3 (`~/.local/bin/make`; the machine had no make before this phase) |
| `.venv/bin/python -c "import torch"` | `ModuleNotFoundError` - torch is absent from the venv, so the passing suite cannot import it (acceptance criterion 4) |
| legacy venv | the pre-existing Python 3.10 testing environment was moved to `.venv-old310` and is git-ignored |

## Lint and tests (acceptance criterion 4)

```
$ make lint
.venv/bin/ruff check .
All checks passed!
lint_exit=0

$ make test
.venv/bin/python -m pytest -q
........................................................................ [ 67%]
...................................                                      [100%]
107 passed in 0.61s
test_exit=0
```

107 tests: config 59, router 12, health 1, static 16, migrations 5, adapter 15. No engine, no
network, no torch.

## Migrations (acceptance criterion 3)

```
$ make migrate                     # fresh database
2026-09-22 14:03:42 layawatch migrations applied: [1]

$ make migrate                     # re-run
2026-09-22 14:03:43 layawatch database schema up to date

$ <bump schema_migrations to 999>; make migrate
2026-09-22 14:03:43 layawatch database schema version 999 is newer than the newest
migration file (1); downgrade LayaWatch to a matching release or restore a database
backup compatible with this release
make: *** [Makefile:17: migrate] Error 3
```

The same guard fired live when the dev server was later pointed at the poisoned
database: `Failed to launch layawatch-dev: failed exit=3`.

## Live server (acceptance criteria 1 and 2)

Process supervised (`layawatch-dev`, pid 2018156, `LAYA_STATE_DIR=/tmp/lw-phase0-dev`), ready in
260 ms on the log line `starting on http`.

```
$ curl -s localhost:8050/healthz
{"status":"ok","models":["english"],"device":"fake","auth_enabled":false}

$ curl -sI localhost:8050/
HTTP/1.1 200 OK
Server: LayaWatch
Content-Type: text/html; charset=utf-8
Cache-Control: no-cache
Content-Length: 988
X-Request-Id: 2dc59e45

$ curl -s localhost:8050/ | grep title
<title>LayaWatch - console not built</title>       # web/out is absent: build page

$ curl -s localhost:8050/api/v1/nope
{"error":{"code":"not_found","message":"not found"}}   # HTTP 404, JSON envelope for API paths

$ curl -s -o /dev/null -w "%{http_code}" localhost:8050/does-not-exist
404                                                 # HTML 404 page for UI paths
```

The served-UI branch of criterion 2 (`web/out` present) cannot run before Phase 4 produces the
export; it is covered by `tests/test_static.py` fixture trees (index resolution, extensionless
routes, ETag/304, immutable assets).

Server-side smoke of server.py (run by the implementing worker, throwaway script outside the
repo): 9/9 - keep-alive reuse, inbound `X-Request-Id: deadbeef` honored, invalid id replaced,
POST without Content-Length -> 411, declared 100000 > max 64 -> 413 without reading the body,
HEAD on GET keeps Content-Length with empty body, SIGTERM -> exit 0 with start/stop log lines.

## Design tokens (acceptance criterion 5)

- Token port self-check (inline python parse of mock `:root`/dark/light blocks vs tokens.css):
  `role diffs: []`, `value diffs: []`, block counts 44/44, 30/30, 27/27.
- In-browser audit: 64/64 rendered swatch colors equal their hex labels, 0 mismatches, 0 console
  errors, columns 720/720 with no overflow, `prefers-reduced-motion` emulation zeroes `--t`.
- Screenshot (this directory): `tokens-preview.png`, full page, both themes side by side.

## Repository (acceptance criterion 6)

| Check | Observed |
|---|---|
| `git remote -v` | `origin https://github.com/BLANCO-11/LayaWatch.git` |
| initial commit | `73fc8bf` (docs and plan); Phase 0 adds this commit |
| `git ls-files "*.json" "*hf-cache*" "*chrome-libs*"` | 0 matches (no state, model cache or harness files tracked) |
| `git status --short` after this commit | clean |
