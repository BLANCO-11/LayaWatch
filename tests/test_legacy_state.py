"""Contract tests for engine/legacy_state.py: real format, verification, idempotency, rename."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

import pytest

from layawatch.engine import legacy_state
from layawatch.engine.keys import auth_armed, verify_key
from layawatch.engine.legacy_state import import_once
from layawatch.log import ring_clear, ring_entries
from layawatch.store.db import connect, migrate

REAL_LEGACY_FILE = Path(__file__).resolve().parent.parent / "api_keys.json"
# The live deployment already ran the one-time import, which renamed the real file.
# The rename preserves bytes verbatim (asserted below), so the .imported artifact holds
# the exact pre-import content and reconstructs the file when the pre-import name is gone.
REAL_IMPORTED_FILE = REAL_LEGACY_FILE.with_name("api_keys.json.imported")

# Exact shape the pre-v0.1.0 server wrote: "laya_" + token_hex(24), prefix = first 12 chars,
# digest = unpeppered sha256 hex, timestamps = local "%Y-%m-%d %H:%M:%S" strings.
LEGACY_SECRET = "laya_" + "ab12cd34ef56" * 4
LEGACY_ID = "deadbeef0001"
LEGACY_CREATED = "2026-09-01 12:00:00"
LEGACY_LAST_USED = "2026-09-02 08:30:00"

LEGACY_ENTRY = {
    "id": LEGACY_ID,
    "name": "legacy ci key",
    "prefix": LEGACY_SECRET[:12],
    "sha256": hashlib.sha256(LEGACY_SECRET.encode()).hexdigest(),
    "created": LEGACY_CREATED,
    "last_used": LEGACY_LAST_USED,
}


@pytest.fixture()
def conn(tmp_path):
    database = connect(tmp_path / "state.sqlite3")
    migrate(database)
    yield database
    database.close()


def write_legacy(tmp_path, keys: list, enabled: bool = True) -> None:
    (tmp_path / "api_keys.json").write_text(
        json.dumps({"enabled": enabled, "keys": keys}, indent=2)
    )


def infos() -> list[str]:
    return [e["message"] for e in ring_entries() if e["level"] == "info"]


def warnings() -> list[str]:
    return [e["message"] for e in ring_entries() if e["level"] == "warning"]


def test_real_repo_file_imports_zero_keys_and_renames(tmp_path, conn) -> None:
    # Source the real bytes: the pre-import file itself when present, else the live
    # deployment's .imported rename (byte-identical by the verbatim-copy contract this
    # test asserts). Stage them under the pre-import name; import_once must import zero
    # keys and rename them - both halves of the contract stay directly observable. With
    # no real artifact at all the real-file observation is impossible: skip with reason
    # rather than fabricate a "real" file.
    if REAL_LEGACY_FILE.exists():
        source = REAL_LEGACY_FILE
    elif REAL_IMPORTED_FILE.exists():
        source = REAL_IMPORTED_FILE
    else:
        pytest.skip("neither api_keys.json nor its api_keys.json.imported rename is present")
    original = json.loads(source.read_text())
    shutil.copy(source, tmp_path / "api_keys.json")
    ring_clear()

    assert import_once(conn, tmp_path) == 0
    assert not (tmp_path / "api_keys.json").exists()
    renamed = tmp_path / "api_keys.json.imported"
    assert renamed.exists()
    assert json.loads(renamed.read_text()) == original  # contents preserved verbatim
    assert conn.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 0
    # The real file ships enabled: false, which lands in settings.
    assert conn.execute(
        "SELECT value FROM settings WHERE key = 'keys.auth'"
    ).fetchone()[0] == "false"
    assert auth_armed(conn) is False
    assert any("imported 0 key(s)" in message for message in infos())


def test_legacy_rows_land_with_normalized_prefix_and_epoch_timestamps(tmp_path, conn) -> None:
    write_legacy(tmp_path, [LEGACY_ENTRY])
    ring_clear()

    assert import_once(conn, tmp_path) == 1
    row = conn.execute(
        "SELECT id, name, prefix, hash, created_at, last_used, request_count,"
        " revoked_at, rate_limit_per_min, burst FROM api_keys"
    ).fetchone()
    assert row[0] == LEGACY_ID
    assert row[1] == "legacy ci key"
    assert row[2] == LEGACY_SECRET[:8]  # documented 8-char prefix; legacy stored 12
    assert row[3] == LEGACY_ENTRY["sha256"]  # digest kept verbatim for dual-format verify
    assert row[4] == time.mktime(time.strptime(LEGACY_CREATED, "%Y-%m-%d %H:%M:%S"))
    assert row[5] == time.mktime(time.strptime(LEGACY_LAST_USED, "%Y-%m-%d %H:%M:%S"))
    assert row[6] == 0
    assert row[7] is None
    assert row[8] is None
    assert row[9] is None

    assert auth_armed(conn) is True  # enabled: true lands in settings
    assert not (tmp_path / "api_keys.json").exists()
    assert (tmp_path / "api_keys.json.imported").exists()
    assert any("imported 1 key(s)" in message for message in infos())


def test_legacy_plaintext_still_verifies_after_import(tmp_path, conn) -> None:
    write_legacy(tmp_path, [LEGACY_ENTRY])
    assert import_once(conn, tmp_path) == 1

    assert verify_key(conn, LEGACY_SECRET, b"any-pepper") == LEGACY_ID
    wrong = LEGACY_SECRET[:10] + ("9" if LEGACY_SECRET[10] != "9" else "8") + LEGACY_SECRET[11:]
    assert verify_key(conn, wrong, b"any-pepper") is None
    # Verification bookkeeping ran through the same transaction path as new keys.
    assert conn.execute("SELECT request_count FROM api_keys").fetchone()[0] == 1


def test_second_call_is_a_noop_and_logs_an_info_line(tmp_path, conn) -> None:
    write_legacy(tmp_path, [LEGACY_ENTRY])
    assert import_once(conn, tmp_path) == 1
    ring_clear()

    assert import_once(conn, tmp_path) == 0  # file already renamed: absent now
    assert conn.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 1
    assert any("api_keys.json already imported; skipping" in message for message in infos())


def test_absent_file_is_a_silent_zero(tmp_path, conn) -> None:
    ring_clear()
    assert import_once(conn, tmp_path) == 0
    assert ring_entries() == []  # a machine that never had legacy state logs nothing
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 0


def test_import_emits_a_deprecation_warning(tmp_path, conn) -> None:
    """Plan phase-8 task 10: v0.1.0 is the one release of compatibility for this import."""
    write_legacy(tmp_path, [LEGACY_ENTRY])
    with pytest.warns(DeprecationWarning, match="api_keys.json import is deprecated"):
        assert import_once(conn, tmp_path) == 1
    assert any("deprecated" in message for message in warnings())


def test_rename_failure_keeps_rows_and_the_next_call_heals(tmp_path, conn, monkeypatch) -> None:
    write_legacy(tmp_path, [LEGACY_ENTRY])
    original_rename = legacy_state._rename

    def deny_rename(src: Path, dst: Path) -> None:
        raise OSError("rename denied")

    monkeypatch.setattr(legacy_state, "_rename", deny_rename)
    ring_clear()
    assert import_once(conn, tmp_path) == 1  # bookkeeping failure does not fail the import
    assert (tmp_path / "api_keys.json").exists()
    assert any("could not rename" in message for message in warnings())

    # The rows committed before the rename: a separate connection sees them.
    other = connect(tmp_path / "state.sqlite3")
    try:
        assert other.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 1
    finally:
        other.close()

    # Next start: insert nothing, finish the rename, stay at one row.
    monkeypatch.setattr(legacy_state, "_rename", original_rename)
    assert import_once(conn, tmp_path) == 0
    assert not (tmp_path / "api_keys.json").exists()
    assert (tmp_path / "api_keys.json.imported").exists()
    assert conn.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 1


def test_malformed_file_is_left_in_place_with_a_warning(tmp_path, conn) -> None:
    (tmp_path / "api_keys.json").write_text("{not json")
    ring_clear()
    assert import_once(conn, tmp_path) == 0
    assert (tmp_path / "api_keys.json").exists()
    assert not (tmp_path / "api_keys.json.imported").exists()
    assert any("api_keys.json" in message for message in warnings())
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 0

    # Wrong shape (enabled must be a boolean, keys must be a list): also left in place.
    (tmp_path / "api_keys.json").write_text('{"enabled": "true", "keys": []}')
    assert import_once(conn, tmp_path) == 0
    assert (tmp_path / "api_keys.json").exists()
    assert not (tmp_path / "api_keys.json.imported").exists()


def test_null_last_used_lands_as_null(tmp_path, conn) -> None:
    entry = {**LEGACY_ENTRY, "id": "cafe00001111", "last_used": None}
    write_legacy(tmp_path, [entry])
    assert import_once(conn, tmp_path) == 1
    row = conn.execute("SELECT last_used FROM api_keys WHERE id = 'cafe00001111'").fetchone()
    assert row[0] is None
