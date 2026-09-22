"""One-time import of the legacy api_keys.json into SQLite (api-reference.md section 14).

The file legacy/serve.py maintained is ``{"enabled": bool, "keys": [{id, name, prefix, sha256,
created, last_used}]}``: digests are plain SHA-256 of the plaintext, prefixes are the first 12
chars of the secret, timestamps are local ``%Y-%m-%d %H:%M:%S`` strings. Import:

- keeps each digest verbatim (engine/keys.py verify_key accepts it alongside the peppered HMAC,
  so every plaintext that verified against the legacy server verifies after import);
- normalizes the prefix to the documented 8-char form (api-reference.md section 9 lists prefix
  as first 8; legacy stored 12);
- converts timestamps to epoch seconds with time.mktime, the inverse of legacy's time.strftime;
- writes the enabled flag to the ``keys.auth`` settings row;
- renames the file to api_keys.json.imported only after the transaction commits.

Idempotency and failure handling: a missing file returns 0, logging an info line when
api_keys.json.imported already exists (the second call after a successful import). A malformed
file is left in place with a warning so the operator can inspect it. INSERT OR IGNORE plus the
post-commit rename make a crash between commit and rename self-healing: the next call re-reads,
inserts nothing, and retries the rename. A rename failure is logged, not raised -- the data is
already committed and the next start finishes the bookkeeping.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from layawatch.engine.keys import AUTH_SETTINGS_KEY
from layawatch.log import get_logger

_log = get_logger("auth")

_STATE_NAME = "api_keys.json"
_IMPORTED_NAME = "api_keys.json.imported"
_LOCAL_FMT = "%Y-%m-%d %H:%M:%S"


def import_once(conn: sqlite3.Connection, state_dir: str | os.PathLike[str]) -> int:
    """Import ``state_dir/api_keys.json`` once; returns the number of key rows inserted.

    0 when the file is absent (never migrated, or already imported). ``conn`` should be a
    connection that is not mid-transaction: keys and the settings row commit together before the
    file is renamed.
    """
    base = Path(state_dir)
    path = base / _STATE_NAME
    if not path.exists():
        if (base / _IMPORTED_NAME).exists():
            _log.info(f"{_STATE_NAME} already imported; skipping")
        return 0

    try:
        data = json.loads(path.read_text())
        keys = data["keys"]
        enabled = data["enabled"]
        if not isinstance(keys, list) or not isinstance(enabled, bool):
            raise ValueError("expected object with a keys list and a boolean enabled flag")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        _log.warning(f"cannot import {_STATE_NAME}: {exc}; file left in place")
        return 0

    now = time.time()
    rows = _key_rows(keys, now)
    before = conn.total_changes
    with conn:
        for row in rows:
            conn.execute(
                "INSERT OR IGNORE INTO api_keys"
                " (id, name, prefix, hash, created_at, last_used, request_count)"
                " VALUES (?, ?, ?, ?, ?, ?, 0)",
                row,
            )
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (AUTH_SETTINGS_KEY, "true" if enabled else "false", now),
        )
    imported = (conn.total_changes - before) - 1  # the settings upsert always changes one row

    _log.info(f"imported {imported} key(s) from {_STATE_NAME}; key auth armed={enabled}")
    try:
        _rename(path, base / _IMPORTED_NAME)
    except OSError as exc:
        _log.warning(f"imported keys but could not rename {_STATE_NAME}: {exc}")
    return imported


def _key_rows(entries: list, now: float) -> list[tuple]:
    """Legacy key entries as api_keys rows; malformed entries are skipped with a warning."""
    rows: list[tuple] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("sha256") or not entry.get("id"):
            _log.warning(f"skipping malformed legacy key entry: {entry!r:.120}")
            continue
        rows.append(
            (
                str(entry["id"]),
                str(entry.get("name") or ""),
                str(entry.get("prefix") or "")[:8],
                str(entry["sha256"]),
                _epoch(entry.get("created"), now),
                _epoch(entry.get("last_used"), None),
            )
        )
    return rows


def _epoch(value: object, fallback: float | None) -> float | None:
    """Legacy local timestamp strings (or epoch numbers) as epoch seconds; else the fallback."""
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return time.mktime(time.strptime(str(value), _LOCAL_FMT))
    except ValueError:
        _log.warning(f"unparseable legacy timestamp {value!r}; using fallback")
        return fallback


def _rename(src: Path, dst: Path) -> None:
    """Post-commit rename; isolated so tests can fail it without patching the os module."""
    os.replace(src, dst)
