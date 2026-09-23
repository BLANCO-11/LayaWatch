# Phase 8 - Packaging, self-host and release: issues

Status: in progress
Severity: blocker | major | minor | nit. Ids: P8-I<NN>.

## Open

| Id | Severity | Summary | Owner | Opened | Notes |
|---|---|---|---|---|---|
| P8-I1 | major | `tests/test_legacy_state.py::test_real_repo_file_imports_zero_keys_and_renames` fails: the live import renamed the repo-root `api_keys.json` to `api_keys.json.imported`, the fixture file is gone | test-refit wave | 2026-09-23 | import path deliberately survives D-016, so the fixture is the gap, not the code; refit wave decides fixture vs test rewrite |

## Closed

| Id | Severity | Summary | Resolution | Closed |
|---|---|---|---|---|
| P8-I2 | minor | backup/restore unusable on hosts without the standalone `sqlite3` CLI (this dev box) | both scripts now prefer `sqlite3` and fall back to `python -m sqlite3` (3.12+), with output normalization for the CLI's tuple rows; requirement documented in operations.md section 1 | 2026-09-23 |
| P8-I3 | minor | first e2e predict failed `400 question payload missing a required field: 'type'` | fixture replaced with the documented smoke input (full laya question shape), valid for real and fake engines | 2026-09-23 |
| P8-I7 | blocker | packaged image crashed at boot: pyproject declared no `package-data`, so `pip install` shipped zero `.sql` files, `migrate()` found no migrations, and startup died on `no such table: settings` (hardened-container probe, read-only run) | `[tool.setuptools.package-data]` now ships `store/schema.sql` + `store/migrations/*.sql`; Dockerfile also places `data/common_passwords.txt` at the `parents[2]` path `auth/passwords.py` resolves in an image (it was missing too); both pinned by `tests/test_container_contract.py`; rebuilt image migrates `[1, 2, 3]`, `/healthz` 200, e2e 12/12 | 2026-09-23 |

## Deferred

| Id | Summary | Why deferred | Revisit in |
|---|---|---|---|
| P8-I4 | release gate run (plan task 14: security checklist 1-12, `make test` full suite, `budget_check all`, UI theme check, pip/npm audit, evidence.md fill) | pinned to run LAST in the test-refit wave with the full suite | test-refit / gate wave |
| P8-I5 | docs finalize + open-question dispositions (task 15 remainder) and `v0.1.0` tag with release notes (task 16) | tag only after tasks 14-15 pass; release notes live in outcome.md at the gate | gate wave |
| P8-I6 | Dockerfile contract tests are static text assertions (no image build in CI) as the plan specifies; image-dependent acceptance (node absent, uid 10001 at runtime, hardened run, image delta measured) needs the gate machine | building the image in CI on every run is explicitly out of scope (plan task 3) | gate wave |
