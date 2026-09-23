# LayaWatch operations guide

Version: 0.1 (2026-09-22)
Audience: whoever runs LayaWatch on a machine they own.

## 1. What you need

| Requirement | Minimum | Notes |
|---|---|---|
| CPU | 4 cores x86-64 | torch CPU inference is the dominant cost |
| RAM | 4 GB | engine checkpoints dominate (english ~800 MB, multilingual ~1.3 GB); LayaWatch adds <= 120 MB |
| Disk | 4 GB | model cache ~2.3 GB plus <= 200 MB of state |
| OS | Linux (tested), macOS (dev) | systemd or Docker for supervision |
| Python | 3.12 | matches the engine's supported range |
| sqlite3 | CLI on `PATH`, any Python 3.12+ | backup/restore scripts use `sqlite3` or the `python -m sqlite3` fallback |
| Docker | 24+ | only for the container path |

## 2. Install paths

### 2.1 From source (development)

```bash
git clone https://github.com/BLANCO-11/LayaWatch.git
cd LayaWatch
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt          # dev tooling (pytest, ruff, playwright)
pip install fastapi uvicorn httpx             # HTTP layer runtime deps (pyproject.toml)
pip install <laya wheel or path>             # engine, pinned version
cd web && npm ci && npm run build && cd ..   # produces web/out
python -m layawatch --port 8050
```

`python -m layawatch` builds the FastAPI app and serves it with uvicorn in the same process —
there is no separate server command. `web/out` is optional for development: without it the API runs
and `/` returns build instructions.

### 2.2 Container (recommended for hosting)

```bash
git clone https://github.com/BLANCO-11/LayaWatch.git
cd LayaWatch
docker compose up -d                # builds ./Dockerfile, starts the layawatch service
docker compose logs -f layawatch
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8050/healthz   # 200
```

The image is built in two stages (Node builds the UI, Python serves everything), runs as a
non-root user (uid 10001) with a read-only root filesystem, and keeps its state in the named
volumes `layawatch-data` (`/data`: `state.sqlite3`, `secret.key`) and `layawatch-models`
(`/models`: checkpoint cache). Every environment key lives inline in `docker-compose.yml` with
its `docs/architecture.md` section 6 default; the compose file binds `8050:8050`, so switch the
published port to `127.0.0.1:8050:8050` when the host is not behind a proxy.

The `WITH_ENGINE` build arg (compose sets `1`) selects the deploy flavor: `1` (default) installs
torch + laya into the image so `POST /predict` runs the real engine out of the box; `0` builds a
console-only image without engine wheels, which boots the fake adapter (`device` reports `fake`).

### 2.3 systemd (no container)

The hardened unit ships as [`layawatch.service`](../layawatch.service) (transcribed from this
section; plan phase-8 task 6):

```bash
sudo cp layawatch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now layawatch
journalctl -u layawatch -f
```

Adjust `WorkingDirectory`, `ExecStart` and `Environment=LAYA_STATE_DIR` if you install outside
`/opt/layawatch` and `/var/lib/layawatch`. The unit text:

```ini
# /etc/systemd/system/layawatch.service
[Unit]
Description=LayaWatch
After=network-online.target

[Service]
User=layawatch
WorkingDirectory=/opt/layawatch
Environment=LAYA_STATE_DIR=/var/lib/layawatch
Environment=LAYWATCH_BIND=127.0.0.1
ExecStart=/opt/layawatch/.venv/bin/python -m layawatch
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/layawatch
LimitNOFILE=4096

[Install]
WantedBy=multi-user.target
```

## 3. First run

1. Start the process. On an empty state directory it creates `state.sqlite3` and `secret.key`
   (0600) under `LAYA_STATE_DIR`, applies migrations, and logs
   `layawatch starting on http://127.0.0.1:8050`.
2. Open `http://127.0.0.1:8050`. With no users yet, the setup wizard (or
   `POST /api/v1/auth/setup`) creates the first owner (email, name, password). Alternatively set
   `LAYWATCH_BOOTSTRAP_OWNER=you@example.com:<password>` for unattended provisioning; it is
   ignored once a user exists.
3. Create an API key for your client, copy it once, then arm key auth for `/predict` and `/route`.
4. Confirm `GET /healthz` reports `status: ok`, the loaded `models`, and `auth_enabled: true`.
5. Optional full-path check against a running instance:
   `python scripts/e2e_smoke.py --base-url http://127.0.0.1:8050` (task 13) walks login, key
   creation, predict, trace visibility, model load/unload and logout, and exits non-zero on the
   first failure.

## 4. Configuration

Full variable list lives in `docs/architecture.md` section 6. The ones operators change most:

| Variable | Default | Change it when |
|---|---|---|
| `LAYWATCH_BIND` | `127.0.0.1` | you have a TLS proxy in front and want LAN access |
| `LAYA_MODELS` | `english,multilingual` | you want a smaller footprint (`english` only) |
| `LAYA_ENGLISH_ONLY` | off | you never serve non-English states and want ~1.3 GB back |
| `LAYWATCH_RETENTION_TRACES` | `10000` | you need longer history and accept disk growth |
| `LAYWATCH_TRACE_SAMPLE` | `1.0` | request volume is high enough that full detail costs too much |
| `LAYWATCH_CAPTURE_PAYLOADS` | `0` | you have a privacy review and need payload inspection |

Settings that are also editable in the UI (retention, sampling, capture, stream tick) are stored in the
`settings` table; environment variables act as defaults for values not yet saved in the database. The
UI states which values came from the environment (locked) versus the database (editable).

## 5. Backups and restore

```bash
# consistent online backup, safe while the service is up (task 7)
scripts/backup.sh                       # STATE_DIR from LAYA_STATE_DIR or /var/lib/layawatch,
                                        # backups to LAYA_BACKUP_DIR or /backup

# inside the container
docker compose exec layawatch scripts/backup.sh /data /backups

# restore on a clean machine (task 8): verifies BEFORE touching the live state
scripts/restore.sh /backup/layawatch-2026-09-22-120000.sqlite3
```

What the scripts guarantee:

- `backup.sh` uses only `VACUUM INTO` (a live `-wal` file is never copied; passing a `-wal` or
  `-shm` path is refused), copies `secret.key` beside the database as
  `layawatch-<stamp>.secret.key`, runs `PRAGMA integrity_check` on the copy and fails unless it
  is `ok`, and prunes backups older than 14 days.
- `restore.sh` stops the service (systemd unit or compose project, `LAYA_RESTORE_SERVICE=none`
  to skip), verifies the backup with `PRAGMA integrity_check` *before* replacing anything,
  replaces `state.sqlite3` (and `secret.key` when the backup carries one), drops stale
  `-wal`/`-shm` files, re-checks integrity, and restarts.
- Both scripts prefer the `sqlite3` CLI and fall back to `python -m sqlite3` (Python 3.12+).

Do not copy a live `-wal` file by hand. Do not back up the model cache unless you need offline
provisioning.

## 6. Upgrade

```bash
scripts/upgrade.sh            # auto-detects the systemd/source path or the compose path
```

What it does (task 8, every step printed):

1. **Preflight (task 10):** refuses to run while a raw `api_keys.json` (not
   `api_keys.json.imported`) sits in `LAYA_STATE_DIR`, with instructions - the legacy import
   ships for one release only, so upgrading past it unrepaired would drop keys.
2. Source path: `git pull`, `cd web && npm ci && npm run build`, then
   `python -m layawatch --migrate-only`, then `systemctl restart layawatch` (when the unit
   exists; otherwise restart your supervisor).
3. Compose path: `docker compose build layawatch`,
   `docker compose run --rm layawatch python -m layawatch --migrate-only`, then
   `docker compose up -d --build`.

Migrations are forward-only and idempotent. The process refuses to start when the database schema is
newer than the code (`schema_migrations` check), with a message telling you to upgrade the code rather
than downgrade the data. Roll back with `scripts/restore.sh` (section 5) plus the previous ref.

## 7. Monitoring LayaWatch

| What | Where |
|---|---|
| Liveness | `GET /healthz` (no auth) |
| Self-metrics | `GET /api/v1/meta` (`obs_dropped_total`, `write_queue_depth`, `db_size_bytes`, `rss_mb`, `sse_clients`) |
| Degraded persistence | UI banner plus `store` log lines at `warn` |
| Audit | UI Users and Audit views, `GET /api/v1/audit` |
| Logs | `journalctl -u layawatch -f` or `docker compose logs -f` |

Alerting guidance: alert on `/healthz` failures, on `obs_dropped_total` increasing, and on
`write_queue_depth` sustained above 1000. LayaWatch deliberately does not ship an alerting engine.

## 8. Resource budgets (tracked from Phase 7, verified at the Phase 8 release gate)

```bash
python scripts/budget_check.py rss        # <= 120 MB after warmup
python scripts/budget_check.py disk       # state dir at 10k traces, <= 200 MB
python scripts/budget_check.py coldstart  # <= 3 s to first served request
python scripts/budget_check.py idle-cpu   # < 2 % over 60 s with no clients
python scripts/budget_check.py image      # runtime image delta over python:3.12-slim + torch,
                                          # <= 50 MB (baseline, delta and verdict printed;
                                          # needs the built image and docker locally)
python scripts/budget_check.py all        # every subcommand above, stops at the first breach
```

Every run writes `bench/results/budget-<name>-<stamp>.json`; `--json` prints a machine-readable
array. If a budget fails, the release does not ship. Tuning order: lower `LAYWATCH_RING_TRACES`,
raise the write batch size, lower `LAYWATCH_RETENTION_TRACES`, then consider `LAYA_MODELS=english`.

## 9. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `address already in use` | another process holds the port | `ss -ltnp \| grep 8050`, change `LAYA_PORT` |
| UI loads, all views say `Failed to load` | API 401: session expired | sign in again; check `secret.key` was not replaced |
| `401` on `/predict` | key auth armed, missing or wrong key | send `X-API-Key`, or disarm key auth in the UI |
| Traces stop appearing | write queue saturated or sampling at 0 | check `write_queue_depth` and `obs_dropped_total` in Settings, disk space |
| Charts empty after a long run | raw traces pruned before rollups were read | check retention settings; rollups are kept 90 days |
| `409` loading multilingual | `LAYA_ENGLISH_ONLY=1` | unset the variable and restart |
| Model load fails with memory error | not enough free RAM | unload another checkpoint first, or lower `LAYA_MODELS` |
| Very high `queue_ms` | torch CPU is single-worker | expected; scale horizontally or lower request concurrency |
| Login loop | cookies blocked, or clock skew | allow first-party cookies, check system time, verify `Secure` behind TLS |
| Container exits immediately | state dir not writable | mount a writable volume at `/data`, check ownership |

## 10. Uninstall

Stop the service, remove the unit or compose project, then delete `/opt/layawatch`, the state directory
and the HF cache if the models are not needed elsewhere. Nothing is registered outside those paths; no
data is sent anywhere.
