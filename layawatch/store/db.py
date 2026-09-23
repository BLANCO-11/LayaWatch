"""SQLite connection factory and migration runner."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_BOOKKEEPING_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
)

#: 4.1 item 4: how many (thread, file) connections one thread may keep alive. The
#: bound exists because tests and scripts open many distinct temporary databases on
#: one thread; the oldest entry is really closed when the limit is passed.
_MAX_CACHED_PER_THREAD = 8


class SchemaTooNewError(Exception):
    """The database schema is newer than the newest available migration file."""


class _CachedConnection(sqlite3.Connection):
    """Connection owned by the per-thread cache behind :func:`connect` (4.1 item 4).

    ``close()`` ends the caller's ownership the way ``sqlite3.Connection.close``
    always did for transactions - it rolls back any open work - but leaves the
    underlying handle open, so the prepared statements pysqlite caches per connection
    survive for this thread's next ``connect()`` to the same file: the per-thread
    prepared-statement cache of the catalog. The handle really closes when the cache
    retires it (over the per-thread bound) or when its thread dies and the connection
    is garbage-collected. ``check_same_thread`` (sqlite3's default) still forbids any
    other thread from touching a cached handle.
    """

    def close(self) -> None:
        if getattr(self, "_retired", False):
            return  # already really closed; a second close is a no-op, as before
        try:
            if self.in_transaction:
                self.rollback()
        except sqlite3.Error:
            # A handle whose transactions can no longer be cleared on this thread must
            # not stay in the cache: fall back to a real close.
            self._retire()

    def _retire(self) -> None:
        """Really close the handle (cache eviction). Idempotent."""
        if not getattr(self, "_retired", False):
            self._retired = True
            sqlite3.Connection.close(self)


class _ThreadConnections(threading.local):
    """Per-thread connection cache; ``__init__`` runs afresh in every new thread."""

    def __init__(self) -> None:
        self.by_path: OrderedDict[str, _CachedConnection] = OrderedDict()


_THREAD_CONNECTIONS = _ThreadConnections()


def _configure(conn: sqlite3.Connection) -> None:
    """Apply the section 4.3 item 11 pragma set - identical on every connection."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-16000")
    conn.execute("PRAGMA mmap_size=134217728")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with the pragmas every LayaWatch connection needs.

    docs/performance.md section 4.3 item 11 lists the full set: journal_mode=WAL,
    synchronous=NORMAL (the documented durability tradeoff, architecture section 5),
    temp_store=MEMORY, cache_size=-16000 (16 MB), mmap_size=134217728 (128 MB) and
    busy_timeout=5000. Readers, writer and retention all go through here, so every
    connection gets the same set instead of the writer alone.

    4.1 item 4: the connection itself is cached per (thread, file), so the prepared
    statements pysqlite keeps per connection (``cached_statements=128``) live across
    requests - the hot read path reuses them instead of re-preparing its SQL every
    time. ``close()`` keeps that cache intact (see ``_CachedConnection``); sqlite3's
    own thread check pins each cached handle to its creating thread. A ``:memory:``
    database is never cached: each ``connect(":memory:")`` is a fresh database by
    sqlite3 contract.
    """
    raw = os.fspath(path)
    if raw == ":memory:":
        conn = sqlite3.connect(path, factory=_CachedConnection, cached_statements=128)
        conn.row_factory = sqlite3.Row
        _configure(conn)
        return conn
    key = os.path.abspath(raw)
    cache = _THREAD_CONNECTIONS.by_path
    conn = cache.get(key)
    if conn is not None:
        cache.move_to_end(key)
        return conn
    conn = sqlite3.connect(path, factory=_CachedConnection, cached_statements=128)
    conn.row_factory = sqlite3.Row
    _configure(conn)
    cache[key] = conn
    while len(cache) > _MAX_CACHED_PER_THREAD:
        _evicted_key, evicted = cache.popitem(last=False)
        evicted._retire()
    return conn


def optimize(conn: sqlite3.Connection) -> None:
    """Run ``PRAGMA optimize`` (docs/performance.md section 4.3 item 18).

    Refreshes query-planner statistics after bulk writes and on shutdown so plans keep
    matching the data. Best-effort by design: a failed stats refresh must never fail or
    roll back the write batch it follows, so ``sqlite3.Error`` is swallowed here.
    """
    try:
        conn.execute("PRAGMA optimize")
    except sqlite3.Error:  # pragma: no cover - depends on an unhealthy connection
        pass


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
