#!/usr/bin/env bash
# Upgrade a LayaWatch install (docs/operations.md section 6).
# Source path: git pull, rebuild web/out, --migrate-only (forward-only, idempotent,
# refuses a newer schema), restart via systemd when the unit manages the install.
# Compose path: rebuild the release image, --migrate-only in a one-off container,
# restart with docker compose up -d --build.
#
# Preflight (plan phase-8 task 10): a raw api_keys.json in the state dir must be
# imported by a v0.1.0 run BEFORE the import path is removed after v0.1.0, so this
# script refuses to continue while one is present.
set -euo pipefail

STATE_DIR="${LAYA_STATE_DIR:-/var/lib/layawatch}"
MODE="${1:-auto}"   # auto | source | compose

step() { printf '[upgrade] %s\n' "$*"; }
die()  { printf '[upgrade] %s\n' "$*" >&2; exit 1; }

# --- preflight: legacy api_keys.json (phase-8 task 10) -----------------------
if [ -f "$STATE_DIR/api_keys.json" ] && [ ! -f "$STATE_DIR/api_keys.json.imported" ]; then
  cat >&2 <<EOF
[upgrade] refused: raw legacy keys file found at $STATE_DIR/api_keys.json.

The api_keys.json import ships for ONE release (v0.1.0) and is removed immediately
after it, so upgrading now would silently drop your keys. Do this first:

  1. run THIS release once against the current state dir (the import runs at
     startup and renames the file to api_keys.json.imported):
       LAYA_STATE_DIR=$STATE_DIR python -m layawatch --migrate-only
  2. start the service and confirm the keys list in the console looks right;
  3. re-run scripts/upgrade.sh.

EOF
  exit 1
fi
step "preflight: no raw api_keys.json in $STATE_DIR"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if [ "$MODE" = "auto" ]; then
  if command -v systemctl >/dev/null && systemctl is-active --quiet layawatch 2>/dev/null; then
    MODE=source
  elif command -v docker >/dev/null && [ -f docker-compose.yml ] \
      && docker compose ps --status running 2>/dev/null | grep -q layawatch; then
    MODE=compose
  elif [ -d .git ]; then
    MODE=source
  else
    die "cannot detect the install path; pass 'source' or 'compose'"
  fi
fi
step "install path: $MODE"

if [ "$MODE" = "compose" ]; then
  step "docker compose build layawatch (release image from this checkout)"
  docker compose build layawatch
  step "migrate only: docker compose run --rm layawatch python -m layawatch --migrate-only"
  docker compose run --rm layawatch python -m layawatch --migrate-only
  step "restart: docker compose up -d --build"
  docker compose up -d --build
else
  step "git pull"
  git pull
  step "build web (npm ci && npm run build)"
  (cd web && npm ci && npm run build)
  PYTHON="${REPO_DIR}/.venv/bin/python"
  [ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
  step "migrate only: $PYTHON -m layawatch --migrate-only"
  "$PYTHON" -m layawatch --migrate-only
  if command -v systemctl >/dev/null && systemctl cat layawatch.service >/dev/null 2>&1; then
    step "restart: systemctl restart layawatch"
    systemctl restart layawatch
  else
    step "no systemd unit found; restart the service yourself (make dev, your supervisor)"
  fi
fi

step "done"
