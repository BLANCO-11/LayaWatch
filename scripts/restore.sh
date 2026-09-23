#!/usr/bin/env bash
# Restore a LayaWatch backup (docs/operations.md section 5).
# Stops the service when a systemd unit or compose project manages it, verifies the
# backup with PRAGMA integrity_check BEFORE touching the live directory, replaces
# state.sqlite3 (plus secret.key when the backup carries one), drops stale
# -wal/-shm files, and restarts. Usage: restore.sh <backup.sqlite3> [state-dir]
set -euo pipefail

BACKUP="${1:?usage: restore.sh <backup.sqlite3> [state-dir]}"
STATE_DIR="${2:-${LAYA_STATE_DIR:-/var/lib/layawatch}}"
DB="$STATE_DIR/state.sqlite3"
SERVICE="${LAYA_RESTORE_SERVICE:-auto}"   # auto | systemd | compose | none

step() { printf '[restore] %s\n' "$*"; }

[ -f "$BACKUP" ] || { echo "[restore] no backup at $BACKUP" >&2; exit 2; }
SQLITE=()
if command -v sqlite3 >/dev/null; then
  SQLITE=(sqlite3)
else
  for py in python3 "$(cd "$(dirname "$0")/.." && pwd)/.venv/bin/python"; do
    if { command -v "$py" >/dev/null 2>&1 || [ -x "$py" ]; } \
        && "$py" -m sqlite3 --help >/dev/null 2>&1; then
      SQLITE=("$py" -m sqlite3)
      break
    fi
  done
fi
[ "${#SQLITE[@]}" -gt 0 ] || { echo "[restore] no sqlite3 CLI and no python with -m sqlite3" >&2; exit 2; }

stop_service() {
  case "$SERVICE" in
    none) step "service stop skipped (LAYA_RESTORE_SERVICE=none)" ;;
    systemd)
      step "systemctl stop layawatch"
      systemctl stop layawatch ;;
    compose)
      step "docker compose stop layawatch"
      docker compose stop layawatch ;;
    auto)
      if command -v systemctl >/dev/null && systemctl is-active --quiet layawatch 2>/dev/null; then
        step "systemctl stop layawatch"
        systemctl stop layawatch
      elif command -v docker >/dev/null && docker compose ps --status running 2>/dev/null \
          | grep -q layawatch; then
        step "docker compose stop layawatch"
        docker compose stop layawatch
      else
        step "no running systemd unit or compose service found; assuming the process is down"
      fi ;;
  esac
}

start_service() {
  case "$SERVICE" in
    none) step "service start skipped (LAYA_RESTORE_SERVICE=none)" ;;
    systemd)
      step "systemctl start layawatch"
      systemctl start layawatch ;;
    compose)
      step "docker compose start layawatch"
      docker compose start layawatch ;;
    auto)
      if command -v systemctl >/dev/null && systemctl cat layawatch.service >/dev/null 2>&1 \
          && systemctl is-enabled --quiet layawatch 2>/dev/null; then
        step "systemctl start layawatch"
        systemctl start layawatch
      elif command -v docker >/dev/null && [ -f docker-compose.yml ]; then
        step "docker compose start layawatch"
        docker compose start layawatch 2>/dev/null \
          || echo "[restore] compose project not running; start it yourself"
      else
        step "no supervisor detected; start the service yourself"
      fi ;;
  esac
}

step "verify backup before touching the live state: $BACKUP"
INTEGRITY="$("${SQLITE[@]}" "$BACKUP" 'PRAGMA integrity_check;')"
# sqlite3 prints `ok`; python -m sqlite3 prints Python row reprs: `('ok',)`.
if [ "$INTEGRITY" != "ok" ] && [ "$INTEGRITY" != "('ok',)" ]; then
  echo "[restore] backup failed integrity_check: $INTEGRITY" >&2
  exit 1
fi
step "integrity_check: ok"

stop_service

step "replace $DB"
mkdir -p "$STATE_DIR"
cp "$BACKUP" "$DB"
rm -f "$DB-wal" "$DB-shm"
step "dropped stale -wal/-shm files"

KEY_BACKUP="${BACKUP%.sqlite3}.secret.key"
if [ -f "$KEY_BACKUP" ]; then
  step "replace secret.key from $KEY_BACKUP"
  cp "$KEY_BACKUP" "$STATE_DIR/secret.key"
  chmod 600 "$STATE_DIR/secret.key"
else
  step "no secret.key beside the backup; keeping the existing one"
fi

step "PRAGMA integrity_check on the restored database"
RESTORED="$("${SQLITE[@]}" "$DB" 'PRAGMA integrity_check;')"
if [ "$RESTORED" != "ok" ] && [ "$RESTORED" != "('ok',)" ]; then
  echo "[restore] restored database failed: $RESTORED" >&2
  exit 1
fi
step "restored database integrity: ok"

start_service
step "done"
