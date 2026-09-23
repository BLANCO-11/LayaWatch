"""Retention: build pending rollups before pruning, apply caps, checkpoint, VACUUM.

docs/architecture.md section 5 (retention paragraph): traces 10,000 rows or 7 days,
logs 2,000 rows or 7 days, rollups 90 days, audit log never, sessions 30 days; rollups
are computed BEFORE raw rows are pruned so charts keep answering pruned windows.

Order inside ``apply`` (one phase per commit so chunked deletes never hold a long
write lock - docs/performance.md section 4.3 items 16/17):

1. Collect the pending rollup keys of the traces that are about to be pruned (age or
   count cap), skipping buckets older than ``rollup_days`` (they would be pruned in the
   same pass). For each pending 10 s key with no row - the writer only ever writes closed
   buckets, so a crash or restart can leave raw traces without rows - build the row from
   the raw traces of that key. Keys that already have a row are the writer's committed
   view and are never touched, so nothing double-counts.
2. Cascade completeness for the parents of pending buckets: a missing or count-mismatched
   60 s / 3600 s row is rebuilt by merging its 10 s children (``merge_stats``); a parent
   whose count equals the sum of its children is already complete and is left alone.
   Counts are integers, so the completeness check and second run are exact no-ops.
3. Chunked deletes (``_DELETE_CHUNK`` rows per statement, committed per chunk): traces
   beyond the count/age caps (observations and scores cascade), logs beyond their caps,
   rollups older than ``rollup_days``, observation input/output payloads older than
   ``payload_ttl_s`` (24 h regardless of trace retention), sessions whose expiry is more
   than 30 days past. ``audit_log`` is never pruned.
4. Commit, then ``PRAGMA wal_checkpoint(TRUNCATE)`` when the writer queue is idle
   (the optional ``queue_idle`` callable reports idleness; omitted means idle), then
   VACUUM only when freelist bytes exceed ``free_page_pct`` of the file, then
   ``PRAGMA optimize`` (docs/performance.md section 4.3 item 18).

Returns a dict of per-action counts. ``run_forever`` drives it from the retention timer.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any

from layawatch.log import get_logger
from layawatch.obs.rollup import floor_bucket, summarize
from layawatch.store import db
from layawatch.store.queries import (
    COUNTER_METRICS,
    counter_stats,
    load_rollup_row,
    merge_stats,
    rollup_row,
    upsert_rollup,
)

_LOG = get_logger("retention")

#: Rows per statement in every chunked delete/update loop, committed per chunk so no
#: statement holds a long write lock (docs/performance.md section 4.3 item 16).
_DELETE_CHUNK = 500
_BUCKET_STEP = 10
_PARENT_STEPS = (60, 3600)
_SESSION_GRACE_DAYS = 30

_RollupKey = tuple[int, str, str, str, int]


def _pending_keys(conn: sqlite3.Connection, age_cutoff: float, max_traces: int,
                  rollup_cutoff: float) -> set[_RollupKey]:
    """Rollup keys of every trace that pruning would remove, minus buckets past
    ``rollup_cutoff`` (building those would be undone by the rollup prune in the same
    pass). Streams both prune predicates; keys are deduplicated in a set."""
    keys: set[_RollupKey] = set()
    predicates = (
        ("ts_start < ?", (age_cutoff,)),
        (
            "rowid IN (SELECT rowid FROM traces ORDER BY ts_start DESC, rowid DESC"
            " LIMIT -1 OFFSET ?)",
            (max_traces,),
        ),
    )
    for where, params in predicates:
        for row in conn.execute(
            f"SELECT ts_start, model, route, status FROM traces WHERE {where}", params
        ):
            bucket = floor_bucket(row[0], _BUCKET_STEP)
            if bucket < rollup_cutoff:
                continue
            model = row[1] or ""
            route = row[2]
            status = row[3]
            keys.add((bucket, "requests", model, route, 0))
            if status >= 400:
                keys.add((bucket, "errors", "", "", status))
            keys.add((bucket, "latency", model, route, 0))
            keys.add((bucket, "queue", model, route, 0))
    return keys


def _gather_stats(conn: sqlite3.Connection, key: _RollupKey) -> dict | None:
    """Rebuild one pending 10 s row's stats from the raw traces of that exact key.

    Counter keys read COUNT(*); distribution keys read the raw duration_ms/queue_ms
    samples. The predicates mirror the writer's grouping: requests/latency/queue split by
    COALESCE(model,'') and route (status dim 0), errors aggregate by status only.
    Returns None when no trace matches (the key vanished under a concurrent prune).
    """
    bucket, metric, model, route, status = key
    window = (bucket, bucket + _BUCKET_STEP)
    if metric in COUNTER_METRICS:
        if metric == "errors":
            row = conn.execute(
                "SELECT COUNT(*) FROM traces WHERE ts_start >= ? AND ts_start < ?"
                " AND status = ?",
                (*window, status),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM traces WHERE ts_start >= ? AND ts_start < ?"
                " AND COALESCE(model, '') = ? AND route = ?",
                (*window, model, route),
            ).fetchone()
        return counter_stats(row[0]) if row[0] else None
    column = "duration_ms" if metric == "latency" else "queue_ms"
    values = [
        sample[0]
        for sample in conn.execute(
            f"SELECT {column} FROM traces WHERE ts_start >= ? AND ts_start < ?"
            " AND COALESCE(model, '') = ? AND route = ?",
            (*window, model, route),
        )
    ]
    return summarize(values) if values else None


def _build_pending(conn: sqlite3.Connection, pending: set[_RollupKey]) -> int:
    """Write missing 10 s rows for pending keys; existing rows are the writer's view."""
    built = 0
    for key in sorted(pending):
        bucket, metric, model, route, status = key
        if load_rollup_row(conn, bucket, _BUCKET_STEP, metric, model, route, status):
            continue
        stats = _gather_stats(conn, key)
        if stats is None:
            continue
        upsert_rollup(conn, rollup_row(bucket, _BUCKET_STEP, metric, model, route, status,
                                       stats))
        built += 1
    return built


def _cascade_parents(conn: sqlite3.Connection, pending: set[_RollupKey]) -> int:
    """Ensure each pending bucket's 60 s / 3600 s parents cover all their 10 s children.

    A parent is rebuilt only when it is missing or its count differs from the sum of its
    children - the exact completeness invariant (docs/architecture.md section 5: rollups
    cascade before raw data is pruned).
    """
    built = 0
    seen: set[_RollupKey] = set()
    for key in sorted(pending):
        bucket, metric, model, route, status = key
        for step in _PARENT_STEPS:
            parent = floor_bucket(bucket, step)
            pkey: _RollupKey = (parent, step, metric, model, route, status)
            if pkey in seen:
                continue
            seen.add(pkey)
            children = conn.execute(
                "SELECT * FROM metric_rollup WHERE step = 10 AND bucket >= ?"
                " AND bucket < ? AND metric = ? AND model = ? AND route = ?"
                " AND status = ?",
                (parent, parent + step, metric, model, route, status),
            ).fetchall()
            if not children:
                continue
            want = sum(int(child["count"]) for child in children)
            existing = load_rollup_row(conn, parent, step, metric, model, route, status)
            if existing is not None and int(existing["count"]) == want:
                continue
            merged: Any = None
            for child in children:
                merged = merge_stats(metric, merged, child)
            upsert_rollup(conn, rollup_row(parent, step, metric, model, route, status,
                                           merged))
            built += 1
    return built


def _prune_traces(conn: sqlite3.Connection, age_cutoff: float, max_traces: int) -> int:
    """Delete traces beyond the count/age caps in ``_DELETE_CHUNK`` chunks."""
    total = 0
    while True:
        cursor = conn.execute(
            "DELETE FROM traces WHERE rowid IN ("
            " SELECT rowid FROM traces WHERE ts_start < ?"
            " OR rowid IN (SELECT rowid FROM traces ORDER BY ts_start DESC, rowid DESC"
            "              LIMIT -1 OFFSET ?)"
            " LIMIT ?)",
            (age_cutoff, max_traces, _DELETE_CHUNK),
        )
        deleted = cursor.rowcount
        total += max(deleted, 0)
        conn.commit()  # release the write lock between chunks (performance.md 4.3.16)
        if deleted < _DELETE_CHUNK:
            return total


def _prune_logs(conn: sqlite3.Connection, age_cutoff: float, max_logs: int) -> int:
    """Delete log entries beyond the count/age caps in chunks."""
    total = 0
    while True:
        cursor = conn.execute(
            "DELETE FROM log_entry WHERE id IN ("
            " SELECT id FROM log_entry WHERE ts < ?"
            " OR id IN (SELECT id FROM log_entry ORDER BY ts DESC, id DESC"
            "           LIMIT -1 OFFSET ?)"
            " LIMIT ?)",
            (age_cutoff, max_logs, _DELETE_CHUNK),
        )
        deleted = cursor.rowcount
        total += max(deleted, 0)
        conn.commit()
        if deleted < _DELETE_CHUNK:
            return total


def _prune_rollups(conn: sqlite3.Connection, rollup_cutoff: float) -> int:
    """Delete every rollup row whose bucket is older than ``rollup_days``, in chunks."""
    total = 0
    while True:
        cursor = conn.execute(
            "DELETE FROM metric_rollup WHERE bucket IN ("
            " SELECT bucket FROM metric_rollup WHERE bucket < ? LIMIT ?)",
            (rollup_cutoff, _DELETE_CHUNK),
        )
        deleted = cursor.rowcount
        total += max(deleted, 0)
        conn.commit()
        if deleted < _DELETE_CHUNK:
            return total


def _null_payloads(conn: sqlite3.Connection, payload_cutoff: float) -> int:
    """Null captured input/output on observations older than the 24 h payload TTL."""
    total = 0
    while True:
        cursor = conn.execute(
            "UPDATE observations SET input = NULL, output = NULL WHERE rowid IN ("
            " SELECT o.rowid FROM observations o JOIN traces t ON o.trace_id = t.id"
            " WHERE t.ts_start < ? AND (o.input IS NOT NULL OR o.output IS NOT NULL)"
            " LIMIT ?)",
            (payload_cutoff, _DELETE_CHUNK),
        )
        updated = cursor.rowcount
        total += max(updated, 0)
        conn.commit()
        if updated < _DELETE_CHUNK:
            return total


def _prune_sessions(conn: sqlite3.Connection, expiry_cutoff: float) -> int:
    """Delete sessions whose expiry is more than the grace period in the past."""
    total = 0
    while True:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE rowid IN ("
            " SELECT rowid FROM sessions WHERE expires_at < ? LIMIT ?)",
            (expiry_cutoff, _DELETE_CHUNK),
        )
        deleted = cursor.rowcount
        total += max(deleted, 0)
        conn.commit()
        if deleted < _DELETE_CHUNK:
            return total


def _checkpoint(conn: sqlite3.Connection, queue_idle: Callable[[], bool] | None) -> int:
    """wal_checkpoint(TRUNCATE) when the writer queue is idle; 1 when truncated."""
    if queue_idle is not None and not queue_idle():
        return 0
    busy, _log_pages, _checkpointed = conn.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    ).fetchone()
    return 0 if busy else 1


def _maybe_vacuum(conn: sqlite3.Connection, free_page_pct: float) -> int:
    """VACUUM only when freelist bytes exceed ``free_page_pct`` of the file."""
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
    free_bytes = free_pages * page_size
    total_bytes = page_count * page_size
    if total_bytes > 0 and free_bytes * 100 > total_bytes * free_page_pct:
        conn.execute("VACUUM")
        return 1
    return 0


def apply(
    conn: sqlite3.Connection,
    *,
    max_traces: int,
    max_logs: int,
    max_age_days: float,
    rollup_days: float = 90,
    payload_ttl_s: float = 86400,
    free_page_pct: float = 20,
    queue_idle: Callable[[], bool] | None = None,
) -> dict:
    """Run one retention pass on ``conn`` and return per-action counts.

    Commits its own work in phases (rollup build, then chunked deletes); the optional
    ``queue_idle`` callable gates the WAL checkpoint on writer idleness (omitted means
    the caller knows the writer is idle).
    """
    now = time.time()
    age_cutoff = now - max_age_days * 86400.0
    rollup_cutoff = now - rollup_days * 86400.0
    payload_cutoff = now - payload_ttl_s
    session_cutoff = now - _SESSION_GRACE_DAYS * 86400.0

    pending = _pending_keys(conn, age_cutoff, max_traces, rollup_cutoff)
    built = _build_pending(conn, pending)
    parents = _cascade_parents(conn, pending)
    conn.commit()

    counts = {
        "pending_rollups_built": built,
        "pending_parents_built": parents,
        "traces_pruned": _prune_traces(conn, age_cutoff, max_traces),
        "logs_pruned": _prune_logs(conn, age_cutoff, max_logs),
        "rollups_pruned": _prune_rollups(conn, rollup_cutoff),
        "payloads_nulled": _null_payloads(conn, payload_cutoff),
        "sessions_pruned": _prune_sessions(conn, session_cutoff),
    }
    conn.commit()
    counts["wal_checkpointed"] = _checkpoint(conn, queue_idle)
    counts["vacuumed"] = _maybe_vacuum(conn, free_page_pct)
    db.optimize(conn)  # 4.3 item 18: PRAGMA optimize after the bulk deletes
    return counts


def run_forever(
    db_path: str,
    stop_event: threading.Event,
    interval: float = 60.0,
    **cfg: Any,
) -> None:
    """Run ``apply`` immediately, then every ``interval`` seconds until ``stop_event``.

    ``stop_event`` is a ``threading.Event``-shaped object (its ``wait`` returns True once
    set), so the SIGTERM path in ``__main__`` stops the timer promptly. Each cycle is
    independent: a failure logs a warning at source ``retention`` and the timer keeps
    running; every cycle opens and closes its own connection.
    """
    if interval <= 0:
        raise ValueError("interval must be > 0")
    while True:
        conn = db.connect(db_path)
        try:
            apply(conn, **cfg)
        except Exception as exc:
            _LOG.warning(f"retention cycle failed: {exc}")
            try:
                conn.rollback()
            except sqlite3.Error:  # pragma: no cover - rollback of a dead connection
                pass
        finally:
            conn.close()
        if stop_event.wait(interval):
            return
