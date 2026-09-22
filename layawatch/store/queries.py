"""Insert and read helpers shared by the writer, retention and later phases.

Write helpers take sequences (one transaction, ``executemany`` per docs/performance.md
section 4.3 item 12); a single item is ``insert_trace(conn, [trace])``. Trace, observation
and score objects are read by attribute only - the writer never imports the recorder
(recorder types appear solely under ``TYPE_CHECKING`` for annotations), so any object with
the column-for-column field names works. ``tags``/``meta`` are JSON-serialized here: the
writer hands over Python values, the tables store TEXT.

Rollup rows follow the ``metric_rollup`` primary key. ``counter_stats`` builds the exact
merge shape for the ``requests``/``errors`` counters (one unit per event; percentiles are
meaningful for distributions only, so they stay NULL and merges add counts), while
distribution metrics use ``layawatch.obs.rollup.summarize``/``merge_rows`` upstream.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from layawatch.obs.rollup import merge_rows

if TYPE_CHECKING:  # pragma: no cover - type-only; the writer must not import the recorder
    from layawatch.obs.recorder import Observation, Score, Trace

_STAT_KEYS = ("count", "sum", "min", "max", "p50", "p90", "p95", "p99")

#: Metrics stored as exact event counters rather than value distributions. Their percentile
#: columns stay NULL and merges add counts; every other metric is a distribution.
COUNTER_METRICS = frozenset({"requests", "errors"})

_TRACE_COLUMNS = (
    "id, ts_start, duration_ms, route, method, status, model, route_reason, lang,"
    " queue_ms, forward_ms, state_bytes, question_count, client_key_id, session_id,"
    " error_code, error_message, tags, meta"
)
_OBSERVATION_COLUMNS = (
    "id, trace_id, parent_id, name, type, start_ms, duration_ms, status, model,"
    " input, output, meta"
)
_SCORE_COLUMNS = "id, trace_id, name, value, data_type, source, comment, ts"
_ROLLUP_COLUMNS = (
    "bucket, step, metric, model, route, status, count, sum, min, max, p50, p90, p95, p99"
)


def insert_trace(conn: sqlite3.Connection, traces: Sequence[Trace]) -> None:
    """Insert finished traces; ``tags`` (list) and ``meta`` (dict) are JSON-serialized."""
    if not traces:
        return
    rows = [
        (
            t.id,
            t.ts_start,
            t.duration_ms,
            t.route,
            t.method,
            t.status,
            t.model,
            t.route_reason,
            t.lang,
            t.queue_ms,
            t.forward_ms,
            t.state_bytes,
            t.question_count,
            t.client_key_id,
            t.session_id,
            t.error_code,
            t.error_message,
            json.dumps(t.tags),
            json.dumps(t.meta),
        )
        for t in traces
    ]
    conn.executemany(f"INSERT INTO traces ({_TRACE_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,"
                     f"?,?,?,?,?,?,?,?,?,?)", rows)


def insert_observations(conn: sqlite3.Connection, observations: Sequence[Observation]) -> None:
    """Insert a trace's observations; ``meta`` (dict or None) is JSON-serialized."""
    if not observations:
        return
    rows = [
        (
            o.id,
            o.trace_id,
            o.parent_id,
            o.name,
            o.type,
            o.start_ms,
            o.duration_ms,
            o.status,
            o.model,
            o.input,
            o.output,
            json.dumps(o.meta) if o.meta is not None else None,
        )
        for o in observations
    ]
    conn.executemany(f"INSERT INTO observations ({_OBSERVATION_COLUMNS}) VALUES"
                     f" (?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def insert_scores(conn: sqlite3.Connection, scores: Sequence[Score]) -> None:
    """Insert the scores attached to a batch of traces."""
    if not scores:
        return
    rows = [
        (s.id, s.trace_id, s.name, s.value, s.data_type, s.source, s.comment, s.ts)
        for s in scores
    ]
    conn.executemany(f"INSERT INTO scores ({_SCORE_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)", rows)


def counter_stats(count: int) -> dict:
    """Counter-row stats for ``count`` unit events: sum equals count, percentiles NULL.

    Every contribution to a ``requests``/``errors`` row is one event, so min and max are 1
    and the percentile columns stay NULL (undefined for counters); merges simply add.
    """
    return {
        "count": int(count),
        "sum": float(count),
        "min": 1.0,
        "max": 1.0,
        "p50": None,
        "p90": None,
        "p95": None,
        "p99": None,
    }


def rollup_row(
    bucket: int,
    step: int,
    metric: str,
    model: str,
    route: str,
    status: int,
    stats: Mapping[str, Any],
) -> dict:
    """Assemble a full ``metric_rollup`` row (primary-key columns plus the eight stats)."""
    return {
        "bucket": bucket,
        "step": step,
        "metric": metric,
        "model": model,
        "route": route,
        "status": status,
        **{key: stats[key] for key in _STAT_KEYS},
    }


def _stats_of(row: Any) -> dict:
    """Normalize an existing rollup row to mergeable stats; NULL percentiles read as 0.0."""
    return {key: (row[key] if row[key] is not None else 0.0) for key in _STAT_KEYS}


def merge_stats(metric: str, existing: Any, new: Mapping[str, Any]) -> dict:
    """Merge one new contribution into an existing row's stats (``existing`` may be None).

    Counters add count and sum exactly; distributions go through ``merge_rows`` (exact
    count/sum/min/max, child-count-weighted percentiles - the documented approximation).
    """
    if existing is None:
        return {key: new[key] for key in _STAT_KEYS}
    if metric in COUNTER_METRICS:
        count = int(existing["count"]) + int(new["count"])
        return counter_stats(count)
    return merge_rows([_stats_of(existing), _stats_of(new)])


def load_rollup_row(
    conn: sqlite3.Connection,
    bucket: int,
    step: int,
    metric: str,
    model: str = "",
    route: str = "",
    status: int = 0,
) -> sqlite3.Row | None:
    """Load one ``metric_rollup`` row by primary key, or None when the bucket is absent."""
    return conn.execute(
        "SELECT * FROM metric_rollup WHERE bucket = ? AND step = ? AND metric = ?"
        " AND model = ? AND route = ? AND status = ?",
        (bucket, step, metric, model, route, status),
    ).fetchone()


def upsert_rollup(conn: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    """Insert or fully replace one ``metric_rollup`` row (the caller computed the merge)."""
    placeholders = ",".join("?" for _ in _ROLLUP_COLUMNS.split(", "))
    conn.execute(
        f"INSERT INTO metric_rollup ({_ROLLUP_COLUMNS}) VALUES ({placeholders})"
        " ON CONFLICT(bucket, step, metric, model, route, status) DO UPDATE SET"
        " count = excluded.count, sum = excluded.sum, min = excluded.min,"
        " max = excluded.max, p50 = excluded.p50, p90 = excluded.p90,"
        " p95 = excluded.p95, p99 = excluded.p99",
        tuple(row[column] for column in _ROLLUP_COLUMNS.split(", ")),
    )


def newest_trace(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Most recently started trace, or None on an empty table (ties broken by rowid)."""
    return conn.execute(
        "SELECT * FROM traces ORDER BY ts_start DESC, rowid DESC LIMIT 1"
    ).fetchone()


def count_traces(conn: sqlite3.Connection) -> int:
    """Number of trace rows currently stored."""
    return int(conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0])


def observations_for(conn: sqlite3.Connection, trace_id: str) -> list[sqlite3.Row]:
    """A trace's observations in waterfall order (start_ms ascending, id tiebreak)."""
    return conn.execute(
        "SELECT * FROM observations WHERE trace_id = ? ORDER BY start_ms, id",
        (trace_id,),
    ).fetchall()
