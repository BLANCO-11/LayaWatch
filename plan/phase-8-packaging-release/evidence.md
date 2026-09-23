# Phase 8 - Packaging, self-host and release: evidence

Status: in progress (remainder-wave runs recorded; gate run per tasks 14-16 pending)
Rule: commands plus observed output. "Looks fine" is not evidence.

## Environment

- Commit: c05b882 + phase-8 remainder working tree (uncommitted)
- Machine: Linux 6.8.0-1064-azure, AMD EPYC 7763, python 3.12.14 (.venv), Docker 29.6.2
- Python / Node / Docker: node 24 toolchain via image build stage; host has no standalone
  `sqlite3` CLI (scripts used the `python -m sqlite3` fallback)

## Runs

| # | Command | Expected | Observed | Date | By |
|---|---|---|---|---|---|
| 1 | `ruff check .` | clean | `All checks passed!` | 2026-09-23 | P8Remainder |
| 2 | `pytest -q` on the 13 touched/new test files (admin-removed, container-contract, config, settings, wire, legacy-state, keys, models, traces, static, middleware, health, engine) | removal + contract green | `174 passed, 1 failed` - the failure is the known env fixture `test_real_repo_file_imports_zero_keys_and_renames` (repo-root `api_keys.json` renamed by the live import; P8-I1, test-refit wave) | 2026-09-23 | P8Remainder |
| 3 | `tsc --noEmit` in `web/` after ShimCountersCard removal | clean | exit 0, no output | 2026-09-23 | P8Remainder |
| 4 | grep sweep `LAYA_ADMIN_TOKEN\|/admin/api\|ShimCounters\|deprecation_counts\|admin_token` outside plan tree | zero live references | survivors only: api-reference 14 removal statement (task 11 mandates it), `tests/test_admin_removed.py` / `test_container_contract.py` assertions, `static.py` `_ADMIN_REMOVED` 404 constants | 2026-09-23 | P8Remainder |
| 5 | `scripts/e2e_smoke.py --base-url http://127.0.0.1:8098` (throwaway local server, state dir = copy of `/tmp/lw-bench`) | 12 steps, exit 0 | PASS: healthz, setup, wrong-password 401 + audit, login, key create, arm, predict (trace `7cbb47fe`), trace visible with 9 spans, model load/unload, logout; exit 0 | 2026-09-23 | P8Remainder |
| 6 | `scripts/backup.sh` against the LIVE throwaway (`LAYA_STATE_DIR=/tmp/lw-e2e LAYA_BACKUP_DIR=/tmp/lw-backup`) | integrity ok, exit 0 | `VACUUM INTO .../layawatch-2026-09-23-023906.sqlite3` + `.secret.key` (0600), `integrity_check: ok`, prune ran, exit 0 (no `-wal` copied) | 2026-09-23 | P8Remainder |
| 7 | `scripts/restore.sh <backup> /tmp/lw-e2e` with `LAYA_RESTORE_SERVICE=none` (server stopped), then restart + probes | verify-before-touch, exit 0 | integrity ok before replace, stale `-wal`/`-shm` dropped, secret.key restored, restored integrity ok, exit 0; after restart `healthz 200` and `POST /api/v1/auth/login` 200 (restored sessions verify) | 2026-09-23 | P8Remainder |
| 8 | `scripts/upgrade.sh` with a raw `api_keys.json` in `LAYA_STATE_DIR` (task 10 preflight) | refuse with instructions, non-zero | exit 1, prints `refused: raw legacy keys file found ...` + the migrate-first steps | 2026-09-23 | P8Remainder |
| 9 | `docker build -t layawatch:0.1.0 .` | image builds | exit 0 (55 s first build; 21 s rebuild after packaging fix) | 2026-09-23 | P8Remainder |
| 10 | `docker run --rm layawatch:0.1.0 node --version` | command not found | exit 127, no node in runtime stage | 2026-09-23 | P8Remainder |
| 11 | `scripts/budget_check.py image` | delta <= 50 MB, printed baseline/delta/verdict | `baseline 41.3 MB (python:3.12-slim 41.3 MB + torch 0.0 MB); image 49.1 MB; delta 7.9 MB; verdict PASS`, table `image 7.9 MB 50 MB PASS`, exit 0, JSON in `bench/results/budget-image-20260923-024627.json` | 2026-09-23 | P8Remainder |
| 12 | hardened run: `docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges -v lw-gate-data:/data -p 127.0.0.1:18050:8050 layawatch:0.1.0` | uid 10001, healthz 200, state under /data only | `id -u` = 10001; `healthz 200 {"status":"ok","models":["english"],"device":"fake","auth_enabled":false}`; log `migrations applied: [1, 2, 3]`; `health=healthy running=true`; no permission errors in logs | 2026-09-23 | P8Remainder |
| 13 | `scripts/e2e_smoke.py --base-url http://127.0.0.1:18050` (fresh hardened container, empty volume) | 12 steps, exit 0 | PASS all 12 (setup created first owner, predict trace `b57903e6` visible with 4 spans, load `['english']` -> unload `[]`), exit 0 | 2026-09-23 | P8Remainder |
| 14 | first hardened probe BEFORE the packaging fix | would have been green | FAILED: `sqlite3.OperationalError: no such table: settings`, container crash-loop - wheel shipped no `.sql` (P8-I7, fixed, probes 12-13 are the after-state) | 2026-09-23 | P8Remainder |

## Artifacts

- screenshots: none this wave (UI theme check belongs to the gate run)
- logs: budget JSON `bench/results/budget-image-20260923-024627.json`; e2e/backup/restore outputs
  transcribed above
- bench JSON / sqlite dumps: image budget JSON kept; pre-fix `budget-image-20260923-024429.json`
  removed (measured the broken image)

## Gaps

- `make test` (full suite), the 12-item security checklist walk, `pip audit`/`npm audit`, the
  light/dark UI theme check and the official compose transcript: deferred to the gate run
  (tasks 14-15, pinned after the test-refit wave) - P8-I4/P8-I5.
- `docker compose up -d` not smoked here: the compose file publishes `8050:8050`, which collides
  with the live hub-managed instance on this box; the gate runs it on a clean machine.
- TLS caddy profile, systemd unit boot, backup pruning >14 days and upgrade's pull/build paths
  need the clean-machine gate (P8-I4).
