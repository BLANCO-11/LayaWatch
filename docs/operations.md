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
| Docker | 24+ | only for the container path |

## 2. Install paths

### 2.1 From source (development)

```bash
git clone https://github.com/BLANCO-11/LayaWatch.git
cd LayaWatch
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt          # dev only; runtime needs no pip packages
pip install <laya wheel or path>             # engine, pinned version
cd web && npm ci && npm run build && cd ..   # produces web/out
python -m layawatch --port 8050
```

`web/out` is optional for development: without it the API runs and `/` returns build instructions.

### 2.2 Container (recommended for hosting)

```bash
docker compose up -d            # deploy/compose.yaml, single service, volume ./data:/data
docker compose logs -f layawatch
```

The image is built in two stages (Node builds the UI, Python serves everything) and runs as a
non-root user with a read-only root filesystem; only `/data` and the model cache are writable.

### 2.3 systemd (no container)

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

1. Start the process. On an empty state directory it creates `state.sqlite3`, `secret.key` (0600) and
   applies migrations.
2. Open `http://127.0.0.1:8050`. With no users yet, the setup wizard creates the first owner
   (email, name, password). Alternatively set `LAYWATCH_BOOTSTRAP_OWNER=you@example.com:<password>`
   for unattended provisioning; it is ignored once a user exists.
3. Create an API key for your client, copy it once, then arm key auth for `/predict` and `/route`.
4. Confirm `/healthz` reports the preloaded checkpoints and `auth_enabled: true`.

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
# consistent online backup, safe while running
sqlite3 /var/lib/layawatch/state.sqlite3 "VACUUM INTO '/backup/layawatch-$(date +%F).sqlite3'"

# restore
systemctl stop layawatch
cp /backup/layawatch-2026-09-22.sqlite3 /var/lib/layawatch/state.sqlite3
rm -f /var/lib/layawatch/state.sqlite3-wal /var/lib/layawatch/state.sqlite3-shm
systemctl start layawatch
```

`secret.key` must be backed up with the database (without it, sessions and API key hashes cannot be
verified). A `scripts/backup.sh` helper wraps the commands, prunes backups older than 14 days, and
verifies the copy with `PRAGMA integrity_check`.

Do not copy a live `-wal` file. Do not back up the model cache unless you need offline provisioning.

## 6. Upgrade

```bash
git pull && cd web && npm ci && npm run build && cd ..
. .venv/bin/activate && python -m layawatch --migrate-only   # applies migrations, exits
systemctl restart layawatch
```

Migrations are forward-only and idempotent. The process refuses to start when the database schema is
newer than the code (`schema_migrations` check), with a message telling you to upgrade the code rather
than downgrade the data.

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
# RSS overhead excluding model weights
python scripts/budget_check.py rss        # asserts <= 120 MB after warmup
python scripts/budget_check.py disk       # loads 10k synthetic traces, asserts <= 200 MB
python scripts/budget_check.py coldstart  # asserts <= 3 s to first served request
python scripts/budget_check.py idle-cpu   # asserts < 2 % over 60 s with no clients
```

If a budget fails, the release does not ship. Tuning order: lower `LAYWATCH_RING_TRACES`, raise the
write batch size, lower `LAYWATCH_RETENTION_TRACES`, then consider `LAYA_MODELS=english`.

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
