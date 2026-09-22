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

The Phase 2 read surface (``list_traces``, ``trace_detail``, ``metrics_series``,
``metrics_summary``, ``metrics_models``, ``logs_list``, ``insert_audit``) validates every
query parameter against an explicit allowlist and raises ``InvalidFilter`` naming the
offending parameter (handlers map it to ``400 invalid_filter``). Pagination is keyset
only: cursors are opaque base64 payloads carrying the sort order, so a page can never
silently switch order mid-scroll. All imports are stdlib or Phase 0/1 modules - the
read layer never imports ``layawatch.http.*``.
"""
from __future__ import annotations

import base64
import json
import math
import sqlite3
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from layawatch.obs.rollup import merge_rows, percentile

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


# ---------------------------------------------------------------------------
# Phase 2 read API: allowlisted filters, keyset cursors, metrics, logs, audit.
# ---------------------------------------------------------------------------


class InvalidFilter(ValueError):
    """Unknown or invalid read-API parameter; the message names the offending parameter.

    Handlers map this to ``HttpError(400, "invalid_filter", str(e))``
    (docs/api-reference.md section 2).
    """


#: Query parameters accepted by ``GET /api/v1/traces``; anything else is an InvalidFilter.
TRACE_FILTERS = frozenset(
    {
        "route",
        "status",
        "model",
        "q",
        "min_duration_ms",
        "tag",
        "since",
        "until",
        "range",
        "limit",
        "cursor",
        "order",
    }
)

#: Query parameters accepted by ``GET /api/v1/logs``.
LOG_FILTERS = frozenset({"level", "q", "trace_id", "since", "until", "range", "limit", "cursor"})

#: Range shorthand tokens -> seconds (docs/api-reference.md section 2).
RANGE_SECONDS = {"15m": 900.0, "1h": 3600.0, "6h": 21600.0, "24h": 86400.0, "7d": 604800.0}

#: Log levels stored by ``layawatch.log`` (implementation levels; docs call "warning" "warn",
#: so ``parse_level`` accepts ``warn`` as an alias).
LOG_LEVELS = ("debug", "info", "warning", "error")

#: Rollup metrics ``GET /api/v1/metrics`` may chart.
METRIC_NAMES = ("requests", "errors", "latency", "queue")

#: Trace/log list pagination defaults (docs/api-reference.md section 2: default 50, max 200).
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

#: Sort orders for the trace list; the cursor payload carries the mode it was issued for.
ORDER_MODES = ("newest", "slowest")

#: Related log lines returned by ``trace_detail`` (bounded tail, newest first).
DETAIL_LOG_LIMIT = 100

#: Section 6 accuracy statement echoed by every ``/api/v1/metrics`` response.
METRICS_NOTE = "percentiles are bucket-level"

#: Persisted rollup steps and the range->step mapping (docs/observability-model.md section 6):
#: <= 15 m -> 10 s, <= 6 h -> 1 m, beyond -> 1 h. The client never re-buckets.
ROLLUP_STEPS = (10, 60, 3600)

_TRACE_LIST_COLUMNS = (
    "id, ts_start, duration_ms, route, method, status, model, route_reason, queue_ms,"
    " forward_ms, client_key_id, error_code"
)
_SERIES_COLUMNS = "bucket, metric, count, sum, min, max, p50, p90, p95, p99"
_LOG_COLUMNS = "id, ts, level, source, trace_id, message"
_ORDER_SQL = {
    "newest": "ts_start DESC, id DESC",
    "slowest": "duration_ms DESC, ts_start DESC, id ASC",
}
# Safety valve: a hand-built window far larger than the allowlisted ranges would otherwise
# materialize unbounded point lists. The largest allowlisted range (7d @ 10s) is 60480.
_MAX_BUCKETS = 100_000


def _first(params: Mapping[str, Any], name: str) -> Any:
    """Raw parameter value; a multi-valued query keeps the first entry (HTTP convention)."""
    value = params.get(name)
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _opt(params: Mapping[str, Any], name: str) -> str | None:
    """Parameter value treated as absent when missing or empty (``?status=`` = no filter)."""
    value = _first(params, name)
    if value is None or value == "":
        return None
    return value


def _reject_unknown(params: Mapping[str, Any], allowlist: frozenset[str]) -> None:
    for key in params:
        if key not in allowlist:
            raise InvalidFilter(f"unknown query parameter {key!r}")


def _parse_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidFilter(f"{name} must be a number") from None
    if not math.isfinite(number):
        raise InvalidFilter(f"{name} must be a finite number")
    return number


def parse_limit(raw: Any, *, default: int = DEFAULT_LIMIT) -> int:
    """Page size: default 50, 1..200 (docs/api-reference.md section 2)."""
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise InvalidFilter("limit must be an integer") from None
    if value < 1:
        raise InvalidFilter("limit must be at least 1")
    if value > MAX_LIMIT:
        raise InvalidFilter(f"limit must be at most {MAX_LIMIT}")
    return value


def parse_range(raw: str) -> float:
    """Range shorthand token -> seconds; unknown tokens are rejected."""
    seconds = RANGE_SECONDS.get(raw)
    if seconds is None:
        raise InvalidFilter(f"range must be one of {', '.join(RANGE_SECONDS)}")
    return seconds


def resolve_window(
    params: Mapping[str, Any], *, now: float | None = None
) -> tuple[float | None, float | None]:
    """Resolve ``since``/``until``/``range`` into a ``(since, until)`` window.

    ``range`` sets ``[now - seconds, now)``; combining it with ``since``/``until`` is
    ambiguous and rejected. Either bound may be None when the caller sent neither.
    """
    now = time.time() if now is None else now
    raw_range = _opt(params, "range")
    since_raw = _opt(params, "since")
    until_raw = _opt(params, "until")
    if raw_range is not None and (since_raw is not None or until_raw is not None):
        raise InvalidFilter("range cannot be combined with since/until")
    if raw_range is not None:
        return (now - parse_range(raw_range), now)
    since = _parse_float(since_raw, "since") if since_raw is not None else None
    until = _parse_float(until_raw, "until") if until_raw is not None else None
    if since is not None and until is not None and since >= until:
        raise InvalidFilter("since must be before until")
    return since, until


def parse_step(raw: Any) -> int | None:
    """Explicit ``step`` parameter: 10, 60 or 3600; None means server-chosen."""
    if raw is None or raw == "":
        return None
    try:
        step = int(raw)
    except (TypeError, ValueError):
        raise InvalidFilter("step must be one of 10, 60, 3600") from None
    if step not in ROLLUP_STEPS:
        raise InvalidFilter("step must be one of 10, 60, 3600")
    return step


def step_for_range(seconds: float) -> int:
    """Server-chosen bucket step for a window: <= 15 m -> 10 s, <= 6 h -> 60 s, else 1 h."""
    if seconds <= RANGE_SECONDS["15m"]:
        return 10
    if seconds <= RANGE_SECONDS["6h"]:
        return 60
    return 3600


def parse_metrics(metrics: Any) -> list[str]:
    """Comma-separated ``metrics`` parameter -> validated, de-duplicated metric names."""
    if not isinstance(metrics, str) or not metrics.strip():
        raise InvalidFilter(f"metrics must name at least one of {', '.join(METRIC_NAMES)}")
    return _validate_metrics([part.strip() for part in metrics.split(",")])


def _validate_metrics(names: Sequence[str]) -> list[str]:
    if not names:
        raise InvalidFilter(f"metrics must name at least one of {', '.join(METRIC_NAMES)}")
    validated: list[str] = []
    for name in names:
        if name not in METRIC_NAMES:
            raise InvalidFilter(
                f"unknown metric {name!r} in metrics; must be one of {', '.join(METRIC_NAMES)}"
            )
        if name not in validated:
            validated.append(name)
    return validated


def parse_status(raw: str) -> int | str:
    """``status`` filter: an exact integer (100..599) or a class like ``4xx``/``5xx``."""
    if raw.isdigit():
        value = int(raw)
        if 100 <= value <= 599:
            return value
        raise InvalidFilter("status must be between 100 and 599 or a class like 4xx")
    if len(raw) == 3 and raw[0] in "12345" and raw[1:] == "xx":
        return raw
    raise InvalidFilter("status must be an integer or a class like 4xx/5xx")


def parse_order(raw: Any) -> str:
    """Trace list sort order: ``newest`` (default) or ``slowest``."""
    if raw is None or raw == "":
        return "newest"
    if raw not in ORDER_MODES:
        raise InvalidFilter("order must be newest or slowest")
    return raw


def parse_level(raw: str | None) -> str | None:
    """Logs ``level`` filter; ``warn`` is accepted as the documented alias of ``warning``."""
    if raw is None:
        return None
    if raw == "warn":
        return "warning"
    if raw not in LOG_LEVELS:
        raise InvalidFilter(f"level must be one of {', '.join(LOG_LEVELS)}")
    return raw


def _like(value: str) -> str:
    """Escape LIKE metacharacters so ``q`` is a literal substring match."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _encode_cursor(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(raw: str) -> dict:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        raise InvalidFilter("cursor is not a valid pagination cursor") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("k"), list):
        raise InvalidFilter("cursor is not a valid pagination cursor")
    return payload


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def list_traces(
    conn: sqlite3.Connection, params: Mapping[str, Any], *, default_limit: int = DEFAULT_LIMIT
) -> dict:
    """Filtered, keyset-paginated trace list (docs/api-reference.md sections 2 and 5).

    Allowlist: route, status, model, q, min_duration_ms, tag, since, until, range, limit,
    cursor, order. ``newest`` keys on ``(ts_start DESC, id DESC)``; ``slowest`` on
    ``(duration_ms DESC, ts_start DESC, id)``; the opaque cursor carries its order mode and
    a cursor used with a conflicting ``order`` is rejected. Returns the pagination
    envelope: items are section 5 summary rows plus ``span_count`` (one grouped COUNT per
    page, never N+1), ``next_cursor`` and ``total_estimate`` (filtered COUNT(*)).
    """
    _reject_unknown(params, TRACE_FILTERS)
    since, until = resolve_window(params)
    limit = parse_limit(_first(params, "limit"), default=default_limit)

    cursor_raw = _opt(params, "cursor")
    payload = _decode_cursor(cursor_raw) if cursor_raw is not None else None

    order_raw = _opt(params, "order")
    if order_raw is not None:
        order = parse_order(order_raw)
        if payload is not None and payload.get("o") != order:
            raise InvalidFilter("cursor was issued for a different order")
    elif payload is not None:
        order = payload.get("o")
        if order not in ORDER_MODES:
            raise InvalidFilter("cursor does not carry a valid order")
    else:
        order = "newest"

    clauses: list[str] = []
    args: list[Any] = []
    if since is not None:
        clauses.append("ts_start >= ?")
        args.append(since)
    if until is not None:
        clauses.append("ts_start < ?")
        args.append(until)

    route = _opt(params, "route")
    if route is not None:
        clauses.append("route = ?")
        args.append(route)
    status_raw = _opt(params, "status")
    if status_raw is not None:
        status = parse_status(status_raw)
        if isinstance(status, int):
            clauses.append("status = ?")
            args.append(status)
        else:  # class like "4xx": a half-open decade range
            low = int(status[0]) * 100
            clauses.append("(status >= ? AND status < ?)")
            args.extend((low, low + 100))
    model = _opt(params, "model")
    if model is not None:
        clauses.append("model = ?")
        args.append(model)
    query = _opt(params, "q")
    if query is not None:
        pattern = _like(query)
        clauses.append("(id LIKE ? ESCAPE '\\' OR error_message LIKE ? ESCAPE '\\')")
        args.extend((pattern, pattern))
    min_duration = _opt(params, "min_duration_ms")
    if min_duration is not None:
        clauses.append("duration_ms >= ?")
        args.append(_parse_float(min_duration, "min_duration_ms"))
    tag = _opt(params, "tag")
    if tag is not None:
        clauses.append("EXISTS (SELECT 1 FROM json_each(traces.tags) je WHERE je.value = ?)")
        args.append(tag)

    # total_estimate counts the filtered set, ignoring the cursor (it is a page offset,
    # not a filter); captured before the cursor predicate is appended below.
    filter_sql = " AND ".join(clauses) if clauses else "1"
    filter_args = list(args)

    if payload is not None:
        keys = payload["k"]
        if order == "newest":
            if len(keys) != 2 or not _is_number(keys[0]) or not isinstance(keys[1], str):
                raise InvalidFilter("cursor is not a valid pagination cursor")
            clauses.append("(ts_start < ? OR (ts_start = ? AND id < ?))")
            args.extend((keys[0], keys[0], keys[1]))
        else:
            if (
                len(keys) != 3
                or not _is_number(keys[0])
                or not _is_number(keys[1])
                or not isinstance(keys[2], str)
            ):
                raise InvalidFilter("cursor is not a valid pagination cursor")
            duration, ts, trace_id = keys
            clauses.append(
                "(duration_ms < ? OR (duration_ms = ? AND ts_start < ?)"
                " OR (duration_ms = ? AND ts_start = ? AND id > ?))"
            )
            args.extend((duration, duration, ts, duration, ts, trace_id))

    where_sql = " AND ".join(clauses) if clauses else "1"
    rows = conn.execute(
        f"SELECT {_TRACE_LIST_COLUMNS} FROM traces WHERE {where_sql}"
        f" ORDER BY {_ORDER_SQL[order]} LIMIT ?",
        [*args, limit + 1],
    ).fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]

    items = [dict(row) for row in page]
    if items:
        ids = [item["id"] for item in items]
        placeholders = ",".join("?" for _ in ids)
        spans = {
            trace_id: count
            for trace_id, count in conn.execute(
                f"SELECT trace_id, COUNT(*) FROM observations"
                f" WHERE trace_id IN ({placeholders}) GROUP BY trace_id",
                ids,
            )
        }
        for item in items:
            item["span_count"] = int(spans.get(item["id"], 0))

    total = int(
        conn.execute(
            f"SELECT COUNT(*) FROM traces WHERE {filter_sql}", filter_args
        ).fetchone()[0]
    )

    next_cursor = None
    if has_more and page:
        last = page[-1]
        if order == "newest":
            next_cursor = _encode_cursor({"o": "newest", "k": [last["ts_start"], last["id"]]})
        else:
            next_cursor = _encode_cursor(
                {"o": "slowest", "k": [last["duration_ms"], last["ts_start"], last["id"]]}
            )
    return {"items": items, "next_cursor": next_cursor, "total_estimate": total}


def _decode_json(value: Any) -> Any:
    """Decode a stored JSON TEXT column; unknown/truncated payloads stay as written."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def trace_detail(conn: sqlite3.Connection, trace_id: str) -> dict | None:
    """One trace with observations, scores, related logs and a computed summary.

    Exactly one query per table (no N+1): traces, observations (waterfall order
    ``start_ms`` ascending per docs/design-language.md 4.15), scores (chronological),
    log_entry (newest, bounded to ``DETAIL_LOG_LIMIT``). The ``summary`` block does the
    math the docs promise server-side: framework overhead, forward/queue shares,
    span count and status class. Returns None when the trace does not exist.
    """
    row = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
    if row is None:
        return None
    trace = dict(row)
    trace["tags"] = _decode_json(trace["tags"])
    trace["meta"] = _decode_json(trace["meta"])

    observations = []
    for obs_row in conn.execute(
        "SELECT * FROM observations WHERE trace_id = ? ORDER BY start_ms, id", (trace_id,)
    ):
        observation = dict(obs_row)
        observation["meta"] = _decode_json(observation["meta"])
        observations.append(observation)

    scores = [
        dict(score_row)
        for score_row in conn.execute(
            "SELECT * FROM scores WHERE trace_id = ? ORDER BY ts, id", (trace_id,)
        )
    ]

    logs = [
        dict(log_row)
        for log_row in conn.execute(
            f"SELECT {_LOG_COLUMNS} FROM log_entry WHERE trace_id = ?"
            f" ORDER BY ts DESC, id DESC LIMIT {DETAIL_LOG_LIMIT}",
            (trace_id,),
        )
    ]

    duration = float(trace["duration_ms"])
    queue = float(trace["queue_ms"])
    forward_value = trace["forward_ms"]
    forward = float(forward_value) if forward_value is not None else 0.0
    summary = {
        "framework_ms": round(max(duration - forward - queue, 0.0), 3),
        "forward_ms": forward_value,
        "forward_share": round(forward / duration, 4) if duration > 0 else 0.0,
        "queue_share": round(queue / duration, 4) if duration > 0 else 0.0,
        "span_count": len(observations),
        "status_class": f"{int(trace['status']) // 100}xx",
    }
    return {
        "trace": trace,
        "observations": observations,
        "scores": scores,
        "logs": logs,
        "summary": summary,
    }


def _bucket_point(metric: str, rows: Sequence[sqlite3.Row], step: int) -> Any:
    """One series point for ``metric`` from the rollup rows sharing a bucket.

    Empty buckets emit points too (counters zero, latency zeros) so charts have no gaps:
    ``requests`` is the per-second rate (count / step), ``errors`` the raw count, ``queue``
    the bucket average and ``latency`` the bucket-level percentile dict.
    """
    if not rows:
        if metric == "latency":
            return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
        if metric == "errors":
            return 0
        return 0.0
    if metric in COUNTER_METRICS:
        count = sum(int(row["count"]) for row in rows)
        return count / step if metric == "requests" else count
    if metric == "queue":
        count = sum(int(row["count"]) for row in rows)
        total = sum(float(row["sum"]) for row in rows)
        return total / count if count else 0.0
    stats = [_stats_of(row) for row in rows]  # latency: percentiles merge child-weighted
    merged = stats[0] if len(stats) == 1 else merge_rows(stats)
    return {name: float(merged[name]) for name in ("p50", "p90", "p95", "p99")}


def metrics_series(
    conn: sqlite3.Connection,
    metrics: Any,
    since: float,
    until: float,
    step: int,
    model: str | None = None,
    route: str | None = None,
) -> dict:
    """Rollup-backed metric series for the exact section 6 response shape.

    Buckets are step-aligned and emitted for the whole window with no gaps, including
    empty ones. ``model``/``route`` filter the dimensioned metrics (requests, latency,
    queue); ``errors`` rollup rows carry no such dimension, so they stay unfiltered.
    """
    names = parse_metrics(metrics) if isinstance(metrics, str) else _validate_metrics(list(metrics))
    try:
        step = int(step)
    except (TypeError, ValueError):
        raise InvalidFilter("step must be one of 10, 60, 3600") from None
    if step not in ROLLUP_STEPS:
        raise InvalidFilter("step must be one of 10, 60, 3600")
    since = _parse_float(since, "since")
    until = _parse_float(until, "until")
    if since >= until:
        raise InvalidFilter("since must be before until")

    floor_start = int(since // step) * step
    placeholders = ",".join("?" for _ in names)
    clauses = ["step = ?", f"metric IN ({placeholders})", "bucket >= ?", "bucket < ?"]
    args: list[Any] = [step, *names, floor_start, until]
    model = model or None
    route = route or None
    if model is not None or route is not None:
        dims = []
        if model is not None:
            dims.append("model = ?")
            args.append(model)
        if route is not None:
            dims.append("route = ?")
            args.append(route)
        clauses.append(f"(metric = 'errors' OR ({' AND '.join(dims)}))")

    by_key: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for row in conn.execute(
        f"SELECT {_SERIES_COLUMNS} FROM metric_rollup WHERE {' AND '.join(clauses)}", args
    ):
        by_key.setdefault((row["bucket"], row["metric"]), []).append(row)

    buckets: list[int] = []
    bucket = floor_start
    while bucket < until:
        buckets.append(bucket)
        bucket += step
        if len(buckets) > _MAX_BUCKETS:
            raise InvalidFilter("step is too small for the requested window")

    series = [
        {
            "metric": name,
            "points": [
                [point, _bucket_point(name, by_key.get((point, name), ()), step)]
                for point in buckets
            ],
        }
        for name in names
    ]
    return {
        "step": step,
        "window": {"since": since, "until": until},
        "series": series,
        "note": METRICS_NOTE,
    }


def _window_stats(rows: Sequence[sqlite3.Row], range_s: float) -> dict:
    """KPI scalars for one window: rate, error count/breakdown, percentiles, queue avg."""
    durations = [float(row["duration_ms"]) for row in rows]
    errors_by_status: dict[int, int] = {}
    queue_total = 0.0
    for row in rows:
        queue_total += float(row["queue_ms"])
        if int(row["status"]) >= 400:
            status = int(row["status"])
            errors_by_status[status] = errors_by_status.get(status, 0) + 1
    count = len(rows)
    return {
        "requests_per_s": count / range_s,
        "errors": sum(errors_by_status.values()),
        "errors_by_status": {
            str(status): errors_by_status[status] for status in sorted(errors_by_status)
        },
        "p50": percentile(durations, 0.5),
        "p95": percentile(durations, 0.95),
        "queue_ms": queue_total / count if count else 0.0,
    }


def metrics_summary(conn: sqlite3.Connection, range_s: float, *, now: float | None = None) -> dict:
    """Overview KPI strip values plus previous-window deltas.

    Computed from ``traces`` (exact, small window), not rollups: the strip needs the
    per-status error breakdown and exact window percentiles (nearest-rank, same method as
    ``obs.rollup.percentile``). ``deltas`` is current minus previous for each windowed
    scalar; ``throttled_5m`` counts 429 traces in the last 5 minutes and is present only
    when non-zero so a healthy deployment carries no permanent zero cell
    (docs/rate-limiting.md section 5).
    """
    now = time.time() if now is None else now
    range_s = _parse_float(range_s, "range")
    if range_s <= 0:
        raise InvalidFilter("range must be positive")

    since = now - range_s
    prev_since = since - range_s
    current_rows = conn.execute(
        "SELECT status, duration_ms, queue_ms FROM traces"
        " WHERE ts_start >= ? AND ts_start < ?",
        (since, now),
    ).fetchall()
    previous_rows = conn.execute(
        "SELECT status, duration_ms, queue_ms FROM traces"
        " WHERE ts_start >= ? AND ts_start < ?",
        (prev_since, since),
    ).fetchall()
    throttled = int(
        conn.execute(
            "SELECT COUNT(*) FROM traces WHERE status = 429 AND ts_start >= ? AND ts_start < ?",
            (now - 300.0, now),
        ).fetchone()[0]
    )
    total = int(conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0])

    current = _window_stats(current_rows, range_s)
    previous = _window_stats(previous_rows, range_s)
    result = {
        "range_s": range_s,
        "window": {"since": since, "until": now},
        "prev_window": {"since": prev_since, "until": since},
        "requests_per_s": current["requests_per_s"],
        "requests_per_s_prev": previous["requests_per_s"],
        "errors": current["errors"],
        "errors_prev": previous["errors"],
        "errors_by_status": current["errors_by_status"],
        "p50": current["p50"],
        "p50_prev": previous["p50"],
        "p95": current["p95"],
        "p95_prev": previous["p95"],
        "queue_ms": current["queue_ms"],
        "queue_ms_prev": previous["queue_ms"],
        "deltas": {
            "requests_per_s": current["requests_per_s"] - previous["requests_per_s"],
            "errors": current["errors"] - previous["errors"],
            "p50": current["p50"] - previous["p50"],
            "p95": current["p95"] - previous["p95"],
            "queue_ms": current["queue_ms"] - previous["queue_ms"],
        },
        "total_requests": total,
    }
    if throttled > 0:
        result["throttled_5m"] = throttled
    return result


def metrics_models(
    conn: sqlite3.Connection,
    since: float | None,
    until: float | None,
    *,
    now: float | None = None,
) -> dict:
    """Model mix buckets for the stacked bar chart (docs/api-reference.md section 6).

    Reads the ``requests`` rollup rows, which are keyed by model: model-less rows
    (``model = ''``) are excluded so the chart legend only names real checkpoints. Buckets
    are emitted for the whole window (empty ones included), and ``total`` equals the sum
    of every bucket's counts. Step is server-chosen from the window; ``since``/``until``
    default to the last 15 minutes when omitted.
    """
    now = time.time() if now is None else now
    if until is None:
        until = now
    if since is None:
        since = until - RANGE_SECONDS["15m"]
    since = _parse_float(since, "since")
    until = _parse_float(until, "until")
    if since >= until:
        raise InvalidFilter("since must be before until")

    step = step_for_range(until - since)
    floor_start = int(since // step) * step
    counts_by_bucket: dict[int, dict[str, int]] = {}
    models: set[str] = set()
    total = 0
    for row in conn.execute(
        "SELECT bucket, model, count FROM metric_rollup"
        " WHERE step = ? AND metric = 'requests' AND model != ''"
        " AND bucket >= ? AND bucket < ?",
        (step, floor_start, until),
    ):
        bucket_counts = counts_by_bucket.setdefault(int(row["bucket"]), {})
        bucket_counts[row["model"]] = bucket_counts.get(row["model"], 0) + int(row["count"])
        models.add(row["model"])
        total += int(row["count"])

    buckets = []
    bucket = floor_start
    while bucket < until:
        buckets.append({"ts": bucket, "counts": counts_by_bucket.get(bucket, {})})
        bucket += step
        if len(buckets) > _MAX_BUCKETS:
            raise InvalidFilter("window is too large for model mix buckets")

    return {
        "step": step,
        "window": {"since": since, "until": until},
        "buckets": buckets,
        "models": sorted(models),
        "total": total,
    }


def logs_list(conn: sqlite3.Connection, params: Mapping[str, Any]) -> dict:
    """Tail of log lines, newest first (docs/api-reference.md section 7).

    Allowlist: level, q, trace_id, since, until, range, limit, cursor. Same pagination
    envelope as the trace list; the keyset cursor is ``(ts DESC, id DESC)``. Items carry
    exactly ``id, ts, level, source, trace_id, message``.
    """
    _reject_unknown(params, LOG_FILTERS)
    since, until = resolve_window(params)
    limit = parse_limit(_first(params, "limit"))
    level = parse_level(_opt(params, "level"))

    clauses: list[str] = []
    args: list[Any] = []
    if since is not None:
        clauses.append("ts >= ?")
        args.append(since)
    if until is not None:
        clauses.append("ts < ?")
        args.append(until)
    if level is not None:
        clauses.append("level = ?")
        args.append(level)
    query = _opt(params, "q")
    if query is not None:
        clauses.append("message LIKE ? ESCAPE '\\'")
        args.append(_like(query))
    trace_id = _opt(params, "trace_id")
    if trace_id is not None:
        clauses.append("trace_id = ?")
        args.append(trace_id)

    filter_sql = " AND ".join(clauses) if clauses else "1"
    filter_args = list(args)

    cursor_raw = _opt(params, "cursor")
    if cursor_raw is not None:
        payload = _decode_cursor(cursor_raw)
        keys = payload["k"]
        if len(keys) != 2 or not _is_number(keys[0]) or not _is_number(keys[1]):
            raise InvalidFilter("cursor is not a valid pagination cursor")
        ts, log_id = keys
        clauses.append("(ts < ? OR (ts = ? AND id < ?))")
        args.extend((ts, ts, log_id))

    where_sql = " AND ".join(clauses) if clauses else "1"
    rows = conn.execute(
        f"SELECT {_LOG_COLUMNS} FROM log_entry WHERE {where_sql}"
        f" ORDER BY ts DESC, id DESC LIMIT ?",
        [*args, limit + 1],
    ).fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [dict(row) for row in page]

    total = int(
        conn.execute(
            f"SELECT COUNT(*) FROM log_entry WHERE {filter_sql}", filter_args
        ).fetchone()[0]
    )

    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor({"k": [last["ts"], last["id"]]})
    return {"items": items, "next_cursor": next_cursor, "total_estimate": total}


def insert_audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    *,
    target: str | None = None,
    result: str = "ok",
    meta: Mapping[str, Any] | None = None,
) -> None:
    """Append one audit entry (``actor`` is a plain string until Phase 3 owns identities).

    ``actor_id`` stays NULL for now; ``meta`` is JSON-serialized. No commit: callers
    already run inside their transaction, like the other insert helpers.
    """
    conn.execute(
        "INSERT INTO audit_log (ts, actor_id, actor, action, target, result, meta)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            time.time(),
            None,
            actor,
            action,
            target,
            result,
            json.dumps(meta) if meta is not None else None,
        ),
    )


# ---------------------------------------------------------------------------
# Trace mutations backing POST /scores, POST /tags and DELETE (api-reference 5).
# ---------------------------------------------------------------------------


def set_trace_tags(conn: sqlite3.Connection, trace_id: str, tags: Sequence[str]) -> None:
    """Persist a trace's tag array; ``tags`` is JSON-serialized. No commit, like the
    other insert helpers - the caller already runs inside its transaction.
    """
    conn.execute("UPDATE traces SET tags = ? WHERE id = ?", (json.dumps(list(tags)), trace_id))


def delete_trace(conn: sqlite3.Connection, trace_id: str) -> bool:
    """Delete one trace. Observations and scores follow through ``ON DELETE CASCADE``;
    ``log_entry`` rows carry no foreign key and are intentionally kept. Returns True when
    a row was removed, False for an unknown id. No commit.
    """
    cursor = conn.execute("DELETE FROM traces WHERE id = ?", (trace_id,))
    return cursor.rowcount > 0
