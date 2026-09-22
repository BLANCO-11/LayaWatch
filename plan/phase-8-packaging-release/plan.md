# Phase 8 - Packaging, self-host and release

Status: planned
Depends on: phase 7
Estimated effort: 4 to 6 days
Deliverable: one non-root container image plus compose, systemd and script install paths verified on
a clean machine, legacy removed, every budget and the security checklist green, and `v0.1.0` tagged
with release notes.

## Objective

Turn the optimized Phase 7 code into a releasable self-hosted product: a multi-stage container image
with no Node at runtime, a compose file with an optional TLS proxy profile, a systemd unit, operator
scripts for backup, restore and upgrade, a hard budget gate, an end-to-end smoke test, the removal of
every legacy shim (D-006), and a release gate that walks every item of `docs/security.md` section 8
before tagging `v0.1.0`.

## Scope

In: `deploy/Dockerfile`, `deploy/compose.yaml`, `deploy/Caddyfile`, `deploy/.env.example`,
`deploy/layawatch.service`, `scripts/backup.sh`, `scripts/restore.sh`, `scripts/upgrade.sh`,
`scripts/budget_check.py` hard gate (including the image delta budget from the resource budget table
in `plan/README.md` section 4, multi-stage shape per `docs/architecture.md` section 8),
`scripts/e2e_smoke.py`, removal of `legacy/` plus the `/admin/api/*` shims and their counters, drop
`LAYA_ADMIN_TOKEN`, `api_keys.json` import path removed after its one release of compatibility,
updates to `docs/api-reference.md` sections 1 and 14, `docs/security.md` sections 3.4 and 8,
`docs/operations.md` sections 2.3, 3, 5, 6 and 8, README quickstart, full-suite plus security-checklist
verification, UI check in both themes, and the `v0.1.0` tag with release notes.

Out: any new feature work, any optimization outside the Phase 7 catalog outcome, hosted or cloud
distribution (GitHub Actions release workflow beyond tagging and CI gates already in place), code
signing and notarization, Windows packaging, a hosted documentation site, split topologies
(`docs/architecture.md` section 8 documents them but they are not built).

## Deliverables

```
deploy/Dockerfile            multi-stage: node:24-slim builds web/out, python:3.12-slim runtime, non-root, read-only rootfs, /data volume, healthcheck
deploy/compose.yaml          single layawatch service, volume, healthcheck, optional caddy TLS profile
deploy/Caddyfile             reverse proxy config for the caddy compose profile
deploy/.env.example          every documented variable with safe defaults and inline comments
deploy/layawatch.service     hardened systemd unit matching docs/operations.md section 2.3
scripts/backup.sh            VACUUM INTO plus secret.key, PRAGMA integrity_check, 14-day retention
scripts/restore.sh           stop, verify integrity, replace state, drop stale -wal/-shm, restart
scripts/upgrade.sh           pull, build web, migrate-only, restart (systemd or compose)
scripts/budget_check.py      hard gate: rss, disk, coldstart, idle-cpu, image delta; any failure exits non-zero
scripts/e2e_smoke.py         login, create key, run predict, assert trace visible, load and unload a model
tests/                       container config, scripts, legacy removal, gate wiring tests
docs/api-reference.md        sections 1 and 14 rewritten: legacy rows gone, v0.1.0 removals recorded
docs/security.md             section 3.4 removed, section 8 verification table added
docs/operations.md           sections 2.3, 3, 5, 6, 8 verified and updated against the scripts
README.md                    quickstart for the three install paths
plan/phase-8-packaging-release/{evidence,issues,decisions,outcome}.md   gate artifacts
```

## Tasks

1. `deploy/Dockerfile` build stage: `FROM node:24-slim AS web`, copy `web/package.json` and
   `web/package-lock.json`, `npm ci`, `npm run build`, export `web/out`; no other repo files enter
   this stage so the layer cache survives Python changes.
2. `deploy/Dockerfile` runtime stage: `FROM python:3.12-slim`, install the pinned torch CPU wheel and
   the pinned `laya` wheel (versions pinned per `plan/README.md` R-07), copy `layawatch/`, the built
   `web/out` and `requirements` metadata, create user `layawatch` (uid 10001, no login shell), set
   `ENV LAYA_STATE_DIR=/data LAYWATCH_BIND=0.0.0.0`, `VOLUME /data`, `EXPOSE 8050`,
   `HEALTHCHECK` polling `GET /healthz` with a start period covering model warmup, `ENTRYPOINT
   ["python", "-m", "layawatch"]`. Node and npm must not appear in this stage; the final image delta
   over the `python:3.12-slim` plus torch baseline must stay <= 50 MB (resource budget table,
   `plan/README.md` section 4).
3. Container hardening verification: run the image with `--read-only`, `--cap-drop ALL`,
   `--security-opt no-new-privileges` and a named volume at `/data`; confirm `/healthz` returns 200,
   the HF model cache lives under `/data` (or a second documented volume) so model download survives
   the read-only root filesystem, and `docker exec <id> id -u` returns 10001. Add `tests/` coverage
   asserting the Dockerfile contract (stage names, non-root user, healthcheck, no `node` binary in
   the runtime stage) so regressions fail in CI without building the image on every run.
4. `deploy/compose.yaml`: one service `layawatch` built from `deploy/Dockerfile`, `env_file:
   deploy/.env.example` pattern documented, `volumes: [./data:/data]` plus the model cache volume,
   `ports: ["127.0.0.1:8050:8050"]`, `healthcheck` against `/healthz`, `restart: unless-stopped`,
   `read_only: true` with the writable volumes, and a `caddy` profile (`profiles: ["tls"]`) adding a
   `caddy` service using `deploy/Caddyfile` with `LAYWATCH_TRUST_PROXY=1` on the layawatch service.
5. `deploy/.env.example`: every variable from the configuration table in `docs/architecture.md`
   section 6 (`LAYA_STATE_DIR`, `LAYA_PORT`, `LAYWATCH_BIND`, `LAYA_MODELS`, `LAYA_ENGLISH_ONLY`,
   `LAYWATCH_TRACE_SAMPLE`, `LAYWATCH_CAPTURE_PAYLOADS`, `LAYWATCH_RETENTION_*`, `LAYWATCH_RING_*`,
   `LAYWATCH_BOOTSTRAP_OWNER`, `LAYWATCH_TRUST_PROXY`, `LAYWATCH_RATELIMIT_*`, `LAYWATCH_MAX_BODY`)
   with defaults matching the docs and a comment stating which ones operators normally change; no
   `LAYA_ADMIN_TOKEN` row (removed in task 9).
6. `deploy/layawatch.service` plus install docs: transcribe the unit from `docs/operations.md`
   section 2.3 (`User=layawatch`, `ProtectSystem=strict`, `ProtectHome=true`,
   `ReadWritePaths=/var/lib/layawatch`, `NoNewPrivileges=true`, `Restart=on-failure`) into the repo,
   and rewrite operations sections 2.3 and 3 so the documented first run (state dir creation,
   `secret.key`, setup wizard or `LAYWATCH_BOOTSTRAP_OWNER`, key creation, `/healthz` with
   `auth_enabled: true`) matches the observed behavior on a clean machine.
7. `scripts/backup.sh`: run `sqlite3 state.sqlite3 "VACUUM INTO ..."`, copy `secret.key` next to the
   database, run `PRAGMA integrity_check` on the produced file and fail if it is not `ok`, prune
   backups older than 14 days, refuse to copy a live `-wal` file, and work for both the systemd path
   (`/var/lib/layawatch`) and the container path (`docker compose exec` or the `/data` volume).
8. `scripts/restore.sh` and `scripts/upgrade.sh`: restore stops the service, verifies the backup with
   `PRAGMA integrity_check` before touching the live directory, replaces `state.sqlite3`, removes
   stale `-wal`/`-shm`, and restarts; upgrade runs `git pull`, `cd web && npm ci && npm run build`
   (or pulls the release image when installed via compose), `python -m layawatch --migrate-only`
   (forward-only, idempotent, refuses a newer schema), then restarts via systemd or
   `docker compose up -d --build`. Both scripts are `set -euo pipefail`, print every step, and are
   documented in `docs/operations.md` sections 5 and 6.
9. Legacy removal, code side: delete `legacy/` (D-006: legacy `serve.py` endpoints removed at Phase
   8), delete `layawatch/api/legacy.py` and its `/admin` redirect, remove the deprecation counters
   from `layawatch/api/meta.py` and from the Settings view in `web/`, remove `LAYA_ADMIN_TOKEN`
   support from `layawatch/config.py` and the auth middleware (an `X-Admin-Token` header now
   authenticates nothing), delete the shim and counter tests and add tests asserting `/admin` and
   `/admin/api/*` return the standard `404 not_found` envelope and that the token env var is ignored.
10. `api_keys.json` import path: `v0.1.0` is the one release of compatibility. Keep
    `layawatch/engine/legacy_state.py` in the tagged tree with a deprecation warning when it fires,
    state in the release notes that the import path is removed immediately after `v0.1.0` (first
    commit of the next cycle, deleting the module and its test), and make `scripts/upgrade.sh`
    preflight fail with explicit instructions when a raw `api_keys.json` (not `.imported`) is found
    in `LAYA_STATE_DIR`, so no operator silently loses keys. Decisions proposed: none, this follows
    D-006 plus the compatibility column already in `docs/api-reference.md` section 14.
11. Doc cutover for the removals: rewrite `docs/api-reference.md` section 1 (drop the `X-Admin-Token`
    automation row) and section 14 (replace the shim table with the statement that all `/admin`
    routes and `LAYA_ADMIN_TOKEN` were removed in `v0.1.0` and that the `api_keys.json` import ends
    after `v0.1.0`), remove section 3.4 from `docs/security.md`, remove the `LAYA_ADMIN_TOKEN` row
    from the `docs/architecture.md` section 6 table, and sweep every doc and script reference with
    one grep for `LAYA_ADMIN_TOKEN`, `/admin/api`, `legacy/` and `api_keys.json`.
12. `scripts/budget_check.py` hard gate: keep the four Phase 7 subcommands (`rss`, `disk`,
    `coldstart`, `idle-cpu`) at their hard thresholds and add `image` comparing the built runtime
    image size against the `python:3.12-slim` plus torch baseline (<= 50 MB delta, printed as
    baseline, delta, verdict); add an `all` mode running every subcommand and exiting non-zero on the
    first failure; update `docs/operations.md` section 8 to list the subcommands actually shipped.
13. `scripts/e2e_smoke.py`: against a running instance (fresh container or local), perform the full
    operator path with `urllib` and a cookie jar: `POST /api/v1/auth/login`, create an API key
    via `POST /api/v1/keys`, arm key auth, `POST /predict` with a fixed English fixture, poll
    `GET /api/v1/traces/{id}` until the trace and its spans are visible, `POST /api/v1/models/load`
    then `POST /api/v1/models/unload`, log out; assert each step's status code and JSON shape, print
    a step-by-step summary, exit non-zero on the first failure. Include one negative step (wrong
    password yields 401 and an audit entry).
14. Release gate run: verify every item of the `docs/security.md` section 8 hardening checklist
    (1 through 12) with the command or test that proves it, run the full suite (`make test`),
    `scripts/budget_check.py all`, `scripts/e2e_smoke.py`, and the UI check in both light and dark
    themes (login, Overview, Traces waterfall, Settings) against the packaged container; record each
    command and its observed output in `plan/phase-8-packaging-release/evidence.md`, including a
    `pip audit` and `npm audit` run with no high or critical findings.
15. Docs finalize: rewrite the README quickstart so all three install paths (source, container,
    systemd) work from a clean machine following only the written steps; walk `docs/operations.md`
    end to end on a fresh container (install, first run, backup, restore, upgrade, troubleshooting);
    resolve the open questions of `plan/README.md` section 10 or carry each unanswered one into the
    release notes (Q-01 license, Q-02 engine pinning, Q-03 always-accounts, Q-04 optional caddy
    profile shipped in task 4, Q-05 opt-in payloads, Q-06 per-key default unlimited).
16. Tag `v0.1.0`: write release notes containing the feature summary, the upgrade procedure
    (`scripts/upgrade.sh`), the rollback procedure (below plus `scripts/restore.sh`), the
    known-limitations list (single-worker torch CPU, in-process rate limit buckets reset on restart,
    no encryption at rest, split topology not built, `api_keys.json` import ends after v0.1.0), and
    the resolved or carried open questions; tag only after tasks 14 and 15 are recorded as passing.

## Acceptance criteria

1. Fresh-container install: on a clean machine, the verbatim steps of `docs/operations.md` section
   2.2 (`docker compose up -d` from `deploy/`) bring the service up, `curl -s -o /dev/null -w '%{http_code}'
   http://127.0.0.1:8050/healthz` returns `200`, the setup wizard creates the first owner, and the
   console loads in the browser; the transcript is in evidence.md.
2. No Node in the runtime image: `docker run --rm <image> node --version` fails with command not
   found, and `scripts/budget_check.py image` exits 0 with the delta over `python:3.12-slim` plus
   torch <= 50 MB, both printed in evidence.
3. Hardened container: the image runs with `--read-only`, `--cap-drop ALL` and a writable volume at
   `/data`, serves `/healthz` 200, runs as uid 10001 (non-root), and writes state only under `/data`;
   the compose healthcheck reaches `healthy` within 60 s of start.
4. TLS profile: `docker compose --profile tls up -d` starts caddy, an `https://` request through the
   proxy reaches the console with `LAYWATCH_TRUST_PROXY=1`, and the default profile (no tls) still
   binds loopback only.
5. systemd path: installing `deploy/layawatch.service` on a machine without Docker, the unit starts,
   `systemctl restart layawatch` survives a restart cycle, `journalctl -u layawatch` shows the
   startup lines, and the first run follows `docs/operations.md` section 3 (bootstrap owner, key
   creation, `/healthz` reporting preloaded checkpoints and `auth_enabled: true`).
6. Backup and restore round trip: `scripts/backup.sh` runs while the service is up, its output file
   passes `PRAGMA integrity_check` with `ok`, backups older than 14 days are pruned, and
   `scripts/restore.sh` on a clean machine restores a backup whose users and traces are visible
   through `/api/v1` after restart; `scripts/upgrade.sh` applies migrations with `--migrate-only` and
   restarts without data loss.
7. Budget gate: `scripts/budget_check.py all` exits 0 on the release machine with recorded numbers:
   RSS overhead <= 120 MB, state dir <= 200 MB at 10k traces, cold start <= 3 s, idle CPU < 2 % over
   60 s, image delta <= 50 MB; any sub-threshold value makes the command exit non-zero.
8. E2E smoke: `scripts/e2e_smoke.py` exits 0 against a fresh container, having logged in, created a
   key, received a 200 from `POST /predict`, found the trace with at least the documented spans in
   `GET /api/v1/traces/{id}`, and completed a model load then unload; with a wrong password it fails
   loudly (401 asserted), not silently.
9. Legacy gone: `GET /admin` and every `GET /admin/api/*` path return the standard `404 not_found`
   envelope, no deprecation counter exists in `/api/v1/meta` or in the Settings view, a mutation with
   `X-Admin-Token` returns `401`, `legacy/` does not exist in the repo or the image, and
   `docs/api-reference.md` sections 1 and 14 no longer document any of them (grep for
   `LAYA_ADMIN_TOKEN` and `/admin/api` across the repo returns no live references outside
   release notes and this plan tree).
10. Security checklist: all 12 items of `docs/security.md` section 8 are marked verified with the
    proving command or test id in evidence.md, and `pip audit` plus `npm audit` report no high or
    critical findings.
11. Test suite and UI: `make test` passes in full, and the manual UI check passes in both themes
    (light and dark) for login, Overview, Traces waterfall and Settings against the packaged
    container, with no contrast regressions against `docs/design-language.md`.
12. Release: tag `v0.1.0` exists and its release notes contain the upgrade procedure, the rollback
    procedure, the known-limitations list, and a disposition for every question in
    `plan/README.md` section 10; the README quickstart has been executed verbatim on a clean machine.

## Evidence required

- `docker compose -f deploy/compose.yaml up -d` transcript plus `curl /healthz` output and
  `docker ps` health status from a clean machine.
- `docker run --rm <image> node --version` (failure) and
  `scripts/budget_check.py image` output: baseline size, delta, verdict.
- `docker inspect` or `docker exec id -u` proving uid 10001, plus the `--read-only` run logs with no
  permission errors.
- `scripts/budget_check.py all` output with every number (rss, disk, coldstart, idle-cpu, image).
- `scripts/e2e_smoke.py` step-by-step output including the trace id and the load/unload audit lines.
- `scripts/backup.sh` output with the integrity check result, a pruned-directory listing, and
  `scripts/restore.sh` output plus post-restore `/api/v1/traces` count.
- `scripts/upgrade.sh` output showing pull, build, migrate-only and restart.
- Security checklist table: each of the 12 `docs/security.md` section 8 items with the command or
  test that proves it and its observed result; `pip audit` and `npm audit` reports.
- `make test` full-suite output; theme check noted with screenshots or a checked table in
  evidence.md.
- `git tag -l v0.1.0` plus the release notes text under
  `plan/phase-8-packaging-release/outcome.md`.

## Risks and mitigations

|Risk|Mitigation|
|---|---|
| Runtime image exceeds the 50 MB delta budget (torch plus `laya` wheels dominate) | multi-stage build keeps npm and caches out; measure with `scripts/budget_check.py image` from the first image commit, not at the gate; cut assets, drop build caches and `.pyc` outside `LAYAWATCH` modules, re-measure before tagging|
| Read-only root filesystem breaks torch or HF cache writes | HF cache redirected to `/data` (or its own volume) via env; verified by the `--read-only` acceptance run in task 3 before any release work proceeds|
| Legacy removal breaks operators still calling `/admin/api/*` or `X-Admin-Token` | shims carried `Deprecation` headers since Phase 2; removal is announced in release notes with a migration table to `/api/v1`; counters showed zero usage before removal is merged|
| Operators lose keys when `api_keys.json` import is removed | `v0.1.0` is the single compatibility release, `scripts/upgrade.sh` preflights for a raw `api_keys.json` and fails with instructions, and the removal lands only after that release (task 10)|
| `backup.sh` copies a live WAL and produces a corrupt backup | only `VACUUM INTO` is used, `PRAGMA integrity_check` on the copy is mandatory and failure is fatal, raw-file copy is refused; restore path tested from a backup taken under load|
| `e2e_smoke.py` flaky on slow machines (model load time) | fixed English checkpoint, generous per-step timeouts with progress output, one retry only on the model-load step, run against a local fresh container in the gate|
| Healthcheck fails during model warmup and restart-loops the container | healthcheck `start-period` covers warmup, `/healthz` answers before models load (documented fields), compose healthcheck tuned and observed in the acceptance run|
| Docs drift from the shipped scripts so a clean machine cannot follow them | tasks 6, 8 and 15 execute the written steps verbatim on a clean machine or fresh container and record the transcript; a stale doc is a defect (working conventions)|

## Rollback

The release candidate is a git tag plus an image, both immutable. A failed gate before the tag
reverts the offending commit (legacy removal and packaging land as separate commits per task) and
re-runs the gate; no data migration is destructive because migrations are forward-only and the
process refuses to start against a newer schema, so rolling back the code means restoring the
matching backup with `scripts/restore.sh`. After the tag, a defective `v0.1.0` is withdrawn by
deleting the tag and re-tagging, and operators are told to stay on the previous commit with the
documented `scripts/upgrade.sh` inverse: check out the previous ref, run `--migrate-only` only if the
schema actually changed (it does not in this phase), restart, and restore the backup if the database
was touched.

## Exit gate

Acceptance criteria 1 to 12 verified with observed commands, every `docs/security.md` section 8 item
marked verified, evidence recorded in `plan/phase-8-packaging-release/evidence.md`, decisions
recorded in `plan/phase-8-packaging-release/decisions.md` (image layout, healthcheck contract,
legacy removal sequencing, api_keys.json compatibility window), issues closed or labeled `deferred`
in `issues.md`, outcome plus release notes in `outcome.md`, `make test` green, and `v0.1.0` tagged.
