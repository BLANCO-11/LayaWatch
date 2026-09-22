"""SQLite connection factory and migration runner."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_BOOKKEEPING_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
)


class SchemaTooNewError(Exception):
    """The database schema is newer than the newest available migration file."""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with the pragmas every LayaWatch connection needs."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _bookkeeping_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    return row is not None


def schema_version(conn: sqlite3.Connection) -> int:
    """Highest recorded migration version, or 0 when the bookkeeping table is absent."""
    if not _bookkeeping_exists(conn):
        return 0
    value = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    return value if value is not None else 0


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending ``*.sql`` migrations in sorted filename order, one transaction each.

    Each applied file is recorded in ``schema_migrations`` (created by this runner) inside
    the same transaction.  Returns the versions applied; a second call is a no-op and
    returns ``[]``.  Raises :class:`SchemaTooNewError` before applying anything when the
    database records a version higher than the newest migration file.
    """
    migrations: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = re.match(r"\d+", path.stem)
        if match is None:
            raise ValueError(f"migration filename must start with a version number: {path.name}")
        migrations.append((int(match.group()), path))

    newest = max((version for version, _ in migrations), default=0)
    current = schema_version(conn)
    if current > newest:
        raise SchemaTooNewError(
            f"database schema version {current} is newer than the newest migration file"
            f" ({newest}); downgrade LayaWatch to a matching release or restore a database"
            " backup compatible with this release"
        )

    recorded: set[int] = set()
    if _bookkeeping_exists(conn):
        recorded = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    pending = [(version, path) for version, path in migrations if version not in recorded]
    if not pending:
        return []

    applied: list[int] = []
    previous_autocommit = conn.autocommit
    conn.autocommit = True
    try:
        for version, path in pending:
            sql = path.read_text(encoding="utf-8")
            conn.execute("BEGIN")
            try:
                conn.executescript(sql)
                conn.execute(_BOOKKEEPING_DDL)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, path.name, datetime.now(timezone.utc).isoformat()),
                )
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            applied.append(version)
    finally:
        conn.autocommit = previous_autocommit
    return applied
