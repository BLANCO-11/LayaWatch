"""Retention tests: caps with rollup completeness, payload TTL, checkpoint, VACUUM, idempotence."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from layawatch.obs.recorder import Observation, Trace
from layawatch.store import db
from layawatch.store.queries import count_traces, insert_observations, insert_trace
from layawatch.store.retention import apply, run_forever
from layawatch.store.writer import Writer


def _migrated(tmp_path: Path, name: str = "state.sqlite3") -> str:
    path = tmp_path / name
    conn = db.connect(path)
    db.migrate(conn)
    conn.close()
    return str(path)


def _trace(
    trace_id: str,
    ts: float,
    *,
    duration_ms: float,
    queue_ms: float = 0.0,
    status: int = 200,
) -> Trace:
    return Trace(
        id=trace_id,
        ts_start=ts,
        duration_ms=duration_ms,
        route="/predict",
        method="POST",
        status=status,
        model="english",
        queue_ms=queue_ms,
        tags=[],
        meta={"sampled": True},
    )


def _obs(obs_id: str, trace_id: str) -> Observation:
    return Observation(
        id=obs_id,
        trace_id=trace_id,
        parent_id=None,
        name="http.receive",
        type="span",
        start_ms=0.0,
        duration_ms=1.0,
        status="ok",
        model=None,
        input=None,
        output=None,
        meta={},
    )


def _window(conn: sqlite3.Connection, step: int, bucket: int,
            metric: str = "latency") -> tuple[int, float]:
    """Chart answer for one window, computed only from rollup rows."""
    row = conn.execute(
        "SELECT COALESCE(SUM(count), 0), COALESCE(SUM(sum), 0) FROM metric_rollup"
        " WHERE step = ? AND bucket = ? AND metric = ?",
        (step, bucket, metric),
    ).fetchone()
    return int(row[0]), float(row[1])


def _state(conn: sqlite3.Connection) -> tuple:
    rollups = [
        tuple(row)
        for row in conn.execute(
            "SELECT bucket, step, metric, model, route, status, count, sum, min, max,"
            " p50, p90, p95, p99 FROM metric_rollup"
            " ORDER BY bucket, step, metric, model, route, status"
        )
    ]
    payloads = [
        tuple(row)
        for row in conn.execute("SELECT id, input, output FROM observations ORDER BY id")
    ]
    sessions = sorted(row[0] for row in conn.execute("SELECT id FROM sessions"))
    audit = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    return (
        count_traces(conn),
        conn.execute("SELECT COUNT(*) FROM log_entry").fetchone()[0],
        rollups,
        payloads,
        sessions,
        audit,
    )


def test_prune_to_cap_keeps_60s_and_1h_window_answers_equal(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    base = int(time.time() // 3600) * 3600 - 3600  # previous hour: buckets are expired
    writer = Writer(path)
    writer.start()
    traces = [
        _trace("00000001", base + 1, duration_ms=10, queue_ms=1),
        _trace("00000002", base + 4, duration_ms=20, queue_ms=3),
        _trace("00000003", base + 8, duration_ms=30, queue_ms=5, status=422),
        _trace("00000004", base + 11, duration_ms=40, queue_ms=7),
        _trace("00000005", base + 15, duration_ms=50, queue_ms=9),
    ]
    for trace in traces:
        writer.enqueue(trace)
    writer.flush_now()
    writer.stop()

    conn = db.connect(path)
    before_60 = _window(conn, 60, base)
    before_1h = _window(conn, 3600, base)
    assert before_60 == (5, 150.0)  # the writer already rolled everything up
    assert before_1h == (5, 150.0)

    counts = apply(conn, max_traces=2, max_logs=100, max_age_days=7)
    assert counts["traces_pruned"] == 3
    assert counts["pending_rollups_built"] == 0  # writer rows exist: no rebuild, no double count
    assert counts["pending_parents_built"] == 0
    assert count_traces(conn) == 2
    assert _window(conn, 60, base) == before_60
    assert _window(conn, 3600, base) == before_1h

    # a tighter second pass prunes the rest; the answers still hold
    counts = apply(conn, max_traces=0, max_logs=100, max_age_days=7)
    assert counts["traces_pruned"] == 2
    assert count_traces(conn) == 0
    assert _window(conn, 60, base) == before_60
    assert _window(conn, 3600, base) == before_1h
    conn.close()


def test_pending_rollups_built_from_raw_traces_before_deletion(tmp_path: Path) -> None:
    # no writer involved: retention must build the rollups itself, cascade first
    path = _migrated(tmp_path)
    conn = db.connect(path)
    base = int(time.time() // 3600) * 3600 - 7200
    traces = [_trace(f"{i:08x}", base + i, duration_ms=10.0 * i) for i in range(10)]
    insert_trace(conn, traces)
    conn.commit()

    first = apply(conn, max_traces=3, max_logs=100, max_age_days=7)
    assert first["traces_pruned"] == 7
    assert first["pending_rollups_built"] > 0
    assert first["pending_parents_built"] > 0
    # built from the full bucket (all 10 raw traces, including the 3 that survive)
    assert _window(conn, 60, base) == (10, 450.0)
    assert _window(conn, 10, base) == (10, 450.0)
    assert _window(conn, 3600, base) == (10, 450.0)

    second = apply(conn, max_traces=0, max_logs=100, max_age_days=7)
    assert second["traces_pruned"] == 3  # the survivors of the first pass
    assert count_traces(conn) == 0
    assert second["pending_rollups_built"] == 0  # rows exist now: untouched, no double count
    assert second["pending_parents_built"] == 0
    assert _window(conn, 60, base) == (10, 450.0)
    conn.close()


def test_second_apply_is_idempotent(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    base = int(time.time() // 3600) * 3600 - 7200
    traces = [_trace(f"{i:08x}", base + i, duration_ms=10.0) for i in range(10)]
    insert_trace(conn, traces)
    conn.executemany(
        "INSERT INTO log_entry (ts, level, source, trace_id, message) VALUES (?,?,?,?,?)",
        [(time.time() - i, "info", "server", None, f"line {i}") for i in range(5)],
    )
    conn.commit()

    first = apply(conn, max_traces=3, max_logs=2, max_age_days=7)
    assert first["traces_pruned"] == 7
    assert first["logs_pruned"] == 3
    assert first["pending_rollups_built"] > 0
    before = _state(conn)

    second = apply(conn, max_traces=3, max_logs=2, max_age_days=7)
    assert second["traces_pruned"] == 0
    assert second["logs_pruned"] == 0
    assert second["rollups_pruned"] == 0
    assert second["payloads_nulled"] == 0
    assert second["sessions_pruned"] == 0
    assert second["pending_rollups_built"] == 0
    assert second["pending_parents_built"] == 0
    assert _state(conn) == before  # the database is bit-for-bit stable
    conn.close()


def test_payload_ttl_nulls_old_capture_markers(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    now = time.time()
    old = _trace("0dd0dd00", now - 2 * 86400, duration_ms=10.0)
    young = _trace("y0ung0001", now - 60, duration_ms=20.0)
    old_obs = _obs("ob0000001", "0dd0dd00")
    old_obs.input = "SECRET_STATE_MARKER"
    old_obs.output = "SECRET_ANSWER_MARKER"
    young_obs = _obs("ob0000002", "y0ung0001")
    young_obs.input = "FRESH_MARKER"
    young_obs.output = "FRESH_ANSWER"
    insert_trace(conn, [old, young])
    insert_observations(conn, [old_obs, young_obs])
    conn.commit()

    counts = apply(conn, max_traces=100, max_logs=100, max_age_days=30)
    assert counts["payloads_nulled"] == 1  # one observation row updated
    assert counts["traces_pruned"] == 0  # the 30-day age cap keeps both traces
    row = conn.execute(
        "SELECT input, output FROM observations WHERE id = 'ob0000001'"
    ).fetchone()
    assert row["input"] is None and row["output"] is None
    row = conn.execute(
        "SELECT input, output FROM observations WHERE id = 'ob0000002'"
    ).fetchone()
    assert row["input"] == "FRESH_MARKER" and row["output"] == "FRESH_ANSWER"
    # no stored payload column still holds the expired marker
    stored = [
        value[0]
        for value in conn.execute(
            "SELECT input FROM observations WHERE input IS NOT NULL"
        ).fetchall()
    ]
    assert "SECRET_STATE_MARKER" not in stored
    conn.close()


def test_audit_log_is_never_pruned(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    now = time.time()
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, result) VALUES (?,?,?,?)",
        (now - 400 * 86400, "system", "key.created", "ok"),
    )
    insert_trace(conn, [_trace("0000000a", now - 3600, duration_ms=5.0)])
    conn.commit()
    counts = apply(conn, max_traces=0, max_logs=0, max_age_days=1)
    assert counts["traces_pruned"] == 1
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
    conn.close()


def test_expired_sessions_pruned_after_the_30_day_grace(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    now = time.time()
    conn.execute(
        "INSERT INTO users (id, email, name, role, password_hash, created_at)"
        " VALUES ('u1', 'a@b.c', 'Owner', 'owner', 'scrypt$x', ?)",
        (now - 60 * 86400,),
    )
    conn.executemany(
        "INSERT INTO sessions (id, user_id, created_at, expires_at, last_seen)"
        " VALUES (?, 'u1', ?, ?, ?)",
        [
            ("dead31d", now - 62 * 86400, now - 31 * 86400, now - 62 * 86400),
            ("dead1d", now - 40 * 86400, now - 86400, now - 2 * 86400),
            ("live", now - 86400, now + 86400, now),
        ],
    )
    conn.commit()
    counts = apply(conn, max_traces=0, max_logs=0, max_age_days=1)
    assert counts["sessions_pruned"] == 1
    remaining = sorted(row[0] for row in conn.execute("SELECT id FROM sessions"))
    assert remaining == ["dead1d", "live"]  # only the >30-days-dead session is gone
    conn.close()


def test_vacuum_skipped_below_the_free_page_threshold(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    insert_trace(conn, [_trace("0000000b", time.time(), duration_ms=5.0)])
    conn.commit()
    counts = apply(
        conn, max_traces=1, max_logs=1, max_age_days=7, free_page_pct=100
    )
    assert counts["vacuumed"] == 0  # 100% free-page threshold can never be exceeded
    conn.close()


def test_vacuum_runs_when_freelist_is_forced_over_threshold(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    # fill the file with rows, then delete them so whole pages land on the freelist
    insert_trace(
        conn, [_trace(f"{i:08x}", time.time(), duration_ms=1.0) for i in range(5000)]
    )
    conn.commit()
    conn.execute("DELETE FROM traces")
    conn.commit()
    free_before = conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert free_before > 0  # crafted: pages really are free

    counts = apply(conn, max_traces=0, max_logs=0, max_age_days=7, free_page_pct=0)
    assert counts["vacuumed"] == 1  # freelist bytes exceed 0% of the file -> invoked
    free_after = conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert free_after < free_before
    conn.close()


def test_wal_checkpoint_runs_when_the_writer_is_idle(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    insert_trace(conn, [_trace("0000000c", time.time(), duration_ms=5.0)])
    conn.commit()
    counts = apply(conn, max_traces=100, max_logs=100, max_age_days=7)
    assert counts["wal_checkpointed"] == 1
    conn.close()


def test_wal_checkpoint_deferred_when_the_writer_is_not_idle(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    counts = apply(
        conn,
        max_traces=100,
        max_logs=100,
        max_age_days=7,
        queue_idle=lambda: False,
    )
    assert counts["wal_checkpointed"] == 0
    conn.close()


def test_run_forever_runs_one_cycle_then_stops(tmp_path: Path) -> None:
    path = _migrated(tmp_path)
    conn = db.connect(path)
    insert_trace(conn, [_trace("0000000d", time.time() - 3600, duration_ms=5.0)])
    conn.commit()
    conn.close()

    stop = threading.Event()
    stop.set()  # pre-set: apply runs once, then wait() returns immediately
    run_forever(path, stop, interval=3600, max_traces=0, max_logs=10, max_age_days=7)

    conn = db.connect(path)
    assert count_traces(conn) == 0  # the single cycle pruned to the cap
    conn.close()
