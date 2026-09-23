#!/usr/bin/env bash
# Consistent online backup of a running LayaWatch (docs/operations.md section 5).
# VACUUM INTO only: a live -wal file is never copied, and the copy must pass
# PRAGMA integrity_check or this script exits non-zero. Works for the systemd path
# (default STATE_DIR=/var/lib/layawatch) and the container path
# (docker compose exec layawatch scripts/backup.sh, where LAYA_STATE_DIR=/data).
set -euo pipefail

STATE_DIR="${1:-${LAYA_STATE_DIR:-/var/lib/layawatch}}"
BACKUP_DIR="${2:-${LAYA_BACKUP_DIR:-/backup}}"
DB="$STATE_DIR/state.sqlite3"

step() { printf '[backup] %s\n' "$*"; }

step "state dir: $STATE_DIR"
step "backup dir: $BACKUP_DIR"

case "$STATE_DIR" in
  *-wal | *-shm) echo "[backup] refused: raw WAL/SHM copy is not a backup; pass the state dir" >&2
    exit 2 ;;
esac
[ -f "$DB" ] || { echo "[backup] no database at $DB" >&2; exit 2; }
# sqlite3 preferred; `python -m sqlite3` (3.12+) is the fallback for hosts without the
# standalone CLI - operations.md section 1 lists both.
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
[ "${#SQLITE[@]}" -gt 0 ] || { echo "[backup] no sqlite3 CLI and no python with -m sqlite3" >&2; exit 2; }

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%F-%H%M%S)"
OUT="$BACKUP_DIR/layawatch-$STAMP.sqlite3"

step "VACUUM INTO $OUT (safe while the service is up)"
"${SQLITE[@]}" "$DB" "VACUUM INTO '$OUT'"

step "copy secret.key next to the database (sessions and API key hashes need it)"
[ -f "$STATE_DIR/secret.key" ] || { echo "[backup] no secret.key in $STATE_DIR" >&2; rm -f "$OUT"; exit 2; }
cp -p "$STATE_DIR/secret.key" "$BACKUP_DIR/layawatch-$STAMP.secret.key"

step "PRAGMA integrity_check on the copy"
INTEGRITY="$("${SQLITE[@]}" "$OUT" 'PRAGMA integrity_check;')"
# sqlite3 prints `ok`; python -m sqlite3 prints Python row reprs: `('ok',)`.
if [ "$INTEGRITY" != "ok" ] && [ "$INTEGRITY" != "('ok',)" ]; then
  echo "[backup] integrity_check failed: $INTEGRITY" >&2
  exit 1
fi
step "integrity_check: ok"

step "prune backups older than 14 days in $BACKUP_DIR"
find "$BACKUP_DIR" -maxdepth 1 -name 'layawatch-*.sqlite3' -mtime +14 -delete
find "$BACKUP_DIR" -maxdepth 1 -name 'layawatch-*.secret.key' -mtime +14 -delete

step "done: $OUT"
ls -l "$BACKUP_DIR"
