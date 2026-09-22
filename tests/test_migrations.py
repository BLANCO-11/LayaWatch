"""Behavioral tests for the migration runner: fresh apply, idempotence, guard, rollback."""
from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from layawatch.store.db import MIGRATIONS_DIR, SchemaTooNewError, connect, migrate, schema_version

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "layawatch" / "store" / "schema.sql"


def _declared_tables() -> set[str]:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    return set(re.findall(r"CREATE\s+TABLE\s+(\w+)", text, flags=re.IGNORECASE))


def _versions(directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Migration versions the directory offers, in apply order (nothing is pinned to
    one file: later phases append migrations and this suite must track them)."""
    versions: list[int] = []
    for path in sorted(directory.glob("*.sql")):
        match = re.match(r"\d+", path.stem)
        assert match is not None
        versions.append(int(match.group()))
    return versions


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def test_fresh_migrate_creates_exactly_the_declared_tables(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.sqlite3")
    versions = _versions()
    applied = migrate(conn)
    assert applied == versions
    assert schema_version(conn) == versions[-1]
    declared = _declared_tables()
    actual = _tables(conn)
    assert actual - declared == set()  # no extra tables beyond schema.sql
    assert declared - actual == set()  # no declared tables missing from the database
    conn.close()


def test_second_migrate_is_a_noop(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.sqlite3")
    versions = _versions()
    first = migrate(conn)
    assert first == versions
    rows_before = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert migrate(conn) == []
    rows_after = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert rows_after == rows_before
    assert schema_version(conn) == versions[-1]
    conn.close()


def test_version_guard_raises_before_applying_anything(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.sqlite3")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (999, 'manual', ?)",
        ("2026-01-01T00:00:00+00:00",),
    )
    conn.commit()
    with pytest.raises(SchemaTooNewError) as excinfo:
        migrate(conn)
    message = str(excinfo.value)
    newest = _versions()[-1]
    assert "999" in message
    assert re.search(rf"\b{newest}\b", message)  # states the newest migration version too
    assert "downgrade" in message.lower()
    assert "restore" in message.lower()
    assert _tables(conn) == {"schema_migrations"}  # no real migration was ever applied
    conn.close()


def test_recorded_rows_match_applied_files(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.sqlite3")
    applied = migrate(conn)
    expected = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = re.match(r"\d+", path.stem)
        assert match is not None
        expected.append((int(match.group()), path.name))
    rows = conn.execute(
        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    assert [(row["version"], row["name"]) for row in rows] == expected
    assert applied == [version for version, _ in expected]
    for row in rows:
        assert datetime.fromisoformat(row["applied_at"]).tzinfo is not None
    conn.close()


def test_failing_migration_rolls_back_its_transaction(tmp_path: Path) -> None:
    broken_dir = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, broken_dir)
    versions = _versions()
    (broken_dir / f"{versions[-1] + 1:04d}_broken.sql").write_text(
        "CREATE TABLE partial_table (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO partial_table (id) VALUES (1);\n"
        "INSERT INTO missing_target VALUES (1);\n",
        encoding="utf-8",
    )
    conn = connect(tmp_path / "state.sqlite3")
    with pytest.raises(sqlite3.Error):
        migrate(conn, broken_dir)
    assert schema_version(conn) == versions[-1]  # real files committed, broken one rolled back
    names = _tables(conn)
    assert "partial_table" not in names  # the failed file left no objects behind
    assert "traces" in names  # the first migration stayed applied
    recorded = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert [row[0] for row in recorded] == _versions()  # every real file, nothing extra
    conn.close()
