"""Phase 2 read API tests: allowlisted filters, keyset cursors, detail math, metrics,
logs and audit entries. All windowed fixtures are hand-computed against a fixed epoch.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from layawatch.obs.rollup import summarize
from layawatch.store import db
from layawatch.store.queries import (
    DEFAULT_LIMIT,
    DETAIL_LOG_LIMIT,
    InvalidFilter,
    counter_stats,
    insert_audit,
    list_traces,
    logs_list,
    metrics_models,
    metrics_series,
    metrics_summary,
    parse_limit,
    parse_metrics,
    resolve_window,
    rollup_row,
    step_for_range,
    trace_detail,
    upsert_rollup,
)

#: Fixed, 10 s-aligned epoch used by every windowed fixture.
NOW = 1_790_000_000.0


def _conn(tmp_path: Path) -> Any:
    conn = db.connect(tmp_path / "read.sqlite3")
    db.migrate(conn)
    return conn


def _insert_trace(
    conn: Any,
    trace_id: str,
    ts: float,
    *,
    duration: float = 100.0,
    route: str = "/predict",
    status: int = 200,
    model: str | None = "english",
    forward_ms: float | None = None,
    queue_ms: float = 0.0,
    route_reason: str | None = None,
    client_key_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    tags: str | None = None,
    meta: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO traces (id, ts_start, duration_ms, route, method, status, model,"
        " route_reason, queue_ms, forward_ms, client_key_id, error_code, error_message,"
        " tags, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            trace_id,
            ts,
            duration,
            route,
            "POST",
            status,
            model,
            route_reason,
            queue_ms,
            forward_ms,
            client_key_id,
            error_code,
            error_message,
            tags,
            meta,
        ),
    )


def _insert_obs(
    conn: Any,
    obs_id: str,
    trace_id: str,
    start_ms: float,
    *,
    name: str = "http.receive",
    meta: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO observations (id, trace_id, parent_id, name, type, start_ms,"
        " duration_ms, status, model, input, output, meta)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (obs_id, trace_id, None, name, "span", start_ms, 1.0, "ok", None, None, None, meta),
    )


def _insert_score(conn: Any, score_id: str, trace_id: str, name: str, value: float,
                  ts: float) -> None:
    conn.execute(
        "INSERT INTO scores (id, trace_id, name, value, data_type, source, comment, ts)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (score_id, trace_id, name, value, "numeric", "human", None, ts),
    )


def _insert_log(
    conn: Any,
    ts: float,
    *,
    level: str = "info",
    source: str = "server",
    trace_id: str | None = None,
    message: str = "line",
) -> int:
    cursor = conn.execute(
        "INSERT INTO log_entry (ts, level, source, trace_id, message) VALUES (?,?,?,?,?)",
        (ts, level, source, trace_id, message),
    )
    return int(cursor.lastrowid)


class _CountingConn:
    """Connection proxy that counts ``execute()`` calls (N+1 guard)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self.executes = 0

    def execute(self, sql: str, *args: Any) -> Any:
        self.executes += 1
        return self._conn.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _roll(conn: Any, bucket: int, metric: str, model: str, route: str, stats: dict,
          *, status: int = 0) -> None:
    upsert_rollup(conn, rollup_row(bucket, 10, metric, model, route, status, stats))


# ---------------------------------------------------------------------------
# list_traces: allowlist matrix
# ---------------------------------------------------------------------------


def test_list_traces_rejects_unknown_parameter(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    with pytest.raises(InvalidFilter, match="bogus"):
        list_traces(conn, {"bogus": "1"})


def test_list_traces_route_filter(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "aa000001", NOW - 1, route="/predict")
    _insert_trace(conn, "aa000002", NOW - 2, route="/route")
    page = list_traces(conn, {"route": "/route"})
    assert [item["id"] for item in page["items"]] == ["aa000002"]
    # an empty value means "no filter", not an error
    assert len(list_traces(conn, {"route": ""})["items"]) == 2


def test_list_traces_status_integer_and_class(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "aa000001", NOW - 1, status=200)
    _insert_trace(conn, "aa000002", NOW - 2, status=422)
    _insert_trace(conn, "aa000003", NOW - 3, status=500)
    _insert_trace(conn, "aa000004", NOW - 4, status=503)

    assert [i["id"] for i in list_traces(conn, {"status": "500"})["items"]] == ["aa000003"]
    assert [i["id"] for i in list_traces(conn, {"status": "4xx"})["items"]] == ["aa000002"]
    assert [i["id"] for i in list_traces(conn, {"status": "5xx"})["items"]] == [
        "aa000003",
        "aa000004",
    ]
    with pytest.raises(InvalidFilter, match="status"):
        list_traces(conn, {"status": "banana"})
    with pytest.raises(InvalidFilter, match="status"):
        list_traces(conn, {"status": "99"})


def test_list_traces_model_filter(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "aa000001", NOW - 1, model="english")
    _insert_trace(conn, "aa000002", NOW - 2, model="multilingual")
    _insert_trace(conn, "aa000003", NOW - 3, model=None)
    assert [i["id"] for i in list_traces(conn, {"model": "english"})["items"]] == ["aa000001"]
    assert [i["id"] for i in list_traces(conn, {"model": "multilingual"})["items"]] == [
        "aa000002"
    ]


def test_list_traces_q_matches_id_or_error_message(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "deadbeef", NOW - 1, error_message="upstream 100% failure")
    _insert_trace(conn, "0dd00000", NOW - 2, error_message="plain refusal")
    _insert_trace(conn, "0dd00001", NOW - 3)

    assert [i["id"] for i in list_traces(conn, {"q": "dead"})["items"]] == ["deadbeef"]
    assert [i["id"] for i in list_traces(conn, {"q": "refusal"})["items"]] == ["0dd00000"]
    # LIKE metacharacters are escaped: "%" matches the literal percent sign only
    assert [i["id"] for i in list_traces(conn, {"q": "%"})["items"]] == ["deadbeef"]


def test_list_traces_min_duration_ms_filter(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "aa000001", NOW - 1, duration=100.0)
    _insert_trace(conn, "aa000002", NOW - 2, duration=900.0)
    page = list_traces(conn, {"min_duration_ms": "500"})
    assert [i["id"] for i in page["items"]] == ["aa000002"]
    with pytest.raises(InvalidFilter, match="min_duration_ms"):
        list_traces(conn, {"min_duration_ms": "fast"})


def test_list_traces_tag_filter_is_exact_json_membership(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "aa000001", NOW - 1, tags='["seed", "prod"]')
    _insert_trace(conn, "aa000002", NOW - 2, tags='["seeded"]')
    _insert_trace(conn, "aa000003", NOW - 3, tags=None)
    assert [i["id"] for i in list_traces(conn, {"tag": "seed"})["items"]] == ["aa000001"]
    # "seed" must not match the element "seeded", and untagged rows never match
    assert list_traces(conn, {"tag": "prod"})["total_estimate"] == 1


def test_list_traces_since_filter(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "aa000001", NOW - 10)
    _insert_trace(conn, "aa000002", NOW - 1000)
    page = list_traces(conn, {"since": str(NOW - 500)})
    assert [i["id"] for i in page["items"]] == ["aa000001"]
    with pytest.raises(InvalidFilter, match="since"):
        list_traces(conn, {"since": "yesterday"})


def test_list_traces_until_filter(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "aa000001", NOW - 10)
    _insert_trace(conn, "aa000002", NOW - 1000)
    page = list_traces(conn, {"until": str(NOW - 500)})
    assert [i["id"] for i in page["items"]] == ["aa000002"]


def test_resolve_window_range_tokens_edges_and_conflicts() -> None:
    for token, seconds in {
        "15m": 900.0,
        "1h": 3600.0,
        "6h": 21600.0,
        "24h": 86400.0,
        "7d": 604800.0,
    }.items():
        assert resolve_window({"range": token}, now=NOW) == (NOW - seconds, NOW)
    with pytest.raises(InvalidFilter, match="range"):
        resolve_window({"range": "30m"}, now=NOW)
    with pytest.raises(InvalidFilter, match="range"):
        resolve_window({"range": "15m", "since": "100"}, now=NOW)
    with pytest.raises(InvalidFilter, match="range"):
        resolve_window({"range": "1h", "until": "200"}, now=NOW)
    with pytest.raises(InvalidFilter, match="since"):
        resolve_window({"since": "100", "until": "100"}, now=NOW)
    with pytest.raises(InvalidFilter, match="until"):
        resolve_window({"until": "later"}, now=NOW)
    assert resolve_window({}, now=NOW) == (None, None)
    assert resolve_window({"since": "100"}, now=NOW) == (100.0, None)


def test_list_traces_range_filters_by_wall_clock(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    fresh = time.time()
    _insert_trace(conn, "aa00000f", fresh - 60)
    _insert_trace(conn, "aa00000e", fresh - 3600)
    page = list_traces(conn, {"range": "15m"})
    assert [i["id"] for i in page["items"]] == ["aa00000f"]
    with pytest.raises(InvalidFilter, match="range"):
        list_traces(conn, {"range": "15m", "until": str(fresh)})


def test_list_traces_limit_validation_and_default(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    for index in range(3):
        _insert_trace(conn, f"aa00000{index}", NOW - index)

    assert parse_limit(None) == DEFAULT_LIMIT == 50
    assert parse_limit("200") == 200
    assert len(list_traces(conn, {}, default_limit=2)["items"]) == 2
    page = list_traces(conn, {"limit": "1"})
    assert len(page["items"]) == 1
    assert page["next_cursor"] is not None

    with pytest.raises(InvalidFilter, match="limit"):
        list_traces(conn, {"limit": "201"})
    with pytest.raises(InvalidFilter, match="limit"):
        list_traces(conn, {"limit": "0"})
    with pytest.raises(InvalidFilter, match="limit"):
        list_traces(conn, {"limit": "abc"})


def test_list_traces_order_slowest(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    # newest trace is the fastest: the two orders must disagree
    _insert_trace(conn, "aa000001", NOW - 1, duration=10.0)
    _insert_trace(conn, "aa000002", NOW - 2, duration=500.0)
    _insert_trace(conn, "aa000003", NOW - 3, duration=200.0)
    assert [i["id"] for i in list_traces(conn, {})["items"]] == [
        "aa000001",
        "aa000002",
        "aa000003",
    ]
    assert [i["id"] for i in list_traces(conn, {"order": "slowest"})["items"]] == [
        "aa000002",
        "aa000003",
        "aa000001",
    ]
    with pytest.raises(InvalidFilter, match="order"):
        list_traces(conn, {"order": "fastest"})


def test_list_traces_newest_cursor_stable_under_insert(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    for index in range(1, 6):
        _insert_trace(conn, f"aa00000{index}", NOW - 10 * index)

    page1 = list_traces(conn, {"limit": "2"})
    assert [i["id"] for i in page1["items"]] == ["aa000001", "aa000002"]
    assert page1["total_estimate"] == 5
    cursor = page1["next_cursor"]
    assert isinstance(cursor, str)

    _insert_trace(conn, "aa000000", NOW - 1)  # newer than everything on page 1

    page2 = list_traces(conn, {"limit": "2", "cursor": cursor})
    assert [i["id"] for i in page2["items"]] == ["aa000003", "aa000004"]  # no dup, no skip
    assert page2["total_estimate"] == 6

    page3 = list_traces(conn, {"limit": "2", "cursor": page2["next_cursor"]})
    assert [i["id"] for i in page3["items"]] == ["aa000005"]
    assert page3["next_cursor"] is None


def test_list_traces_slowest_cursor_stable_under_insert(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    for index in range(1, 6):
        _insert_trace(conn, f"aa00000{index}", NOW - 10 * index, duration=100.0 * index)

    page1 = list_traces(conn, {"order": "slowest", "limit": "2"})
    assert [i["id"] for i in page1["items"]] == ["aa000005", "aa000004"]
    cursor = page1["next_cursor"]

    _insert_trace(conn, "aa000000", NOW - 1, duration=600.0)  # slower than page 1

    page2 = list_traces(conn, {"order": "slowest", "limit": "2", "cursor": cursor})
    assert [i["id"] for i in page2["items"]] == ["aa000003", "aa000002"]

    page3 = list_traces(conn, {"order": "slowest", "limit": "2", "cursor": page2["next_cursor"]})
    assert [i["id"] for i in page3["items"]] == ["aa000001"]
    assert page3["next_cursor"] is None


def test_list_traces_cursor_carries_order_mode(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    for index in range(1, 4):
        _insert_trace(conn, f"aa00000{index}", NOW - 10 * index, duration=100.0 * index)

    cursor = list_traces(conn, {"order": "slowest", "limit": "1"})["next_cursor"]
    with pytest.raises(InvalidFilter, match="cursor"):
        list_traces(conn, {"order": "newest", "cursor": cursor})
    # without an explicit order the cursor's mode governs the continuation
    continuation = list_traces(conn, {"cursor": cursor})
    assert [i["duration_ms"] for i in continuation["items"]] == [200.0, 100.0]
    with pytest.raises(InvalidFilter, match="cursor"):
        list_traces(conn, {"cursor": "@@@"})


def test_list_traces_summary_item_shape_and_span_count(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "aa000001", NOW - 1, forward_ms=80.0, queue_ms=5.0,
                  client_key_id="k_3f8a", route_reason="state is English")
    _insert_trace(conn, "aa000002", NOW - 2)
    _insert_obs(conn, "o1", "aa000001", 0.0)
    _insert_obs(conn, "o2", "aa000001", 5.0)
    _insert_obs(conn, "o3", "aa000001", 10.0)

    counting = _CountingConn(conn)
    page = list_traces(counting, {})
    # main page query + one grouped span COUNT + one filtered total COUNT (no N+1)
    assert counting.executes == 3
    assert set(page) == {"items", "next_cursor", "total_estimate"}
    assert page["total_estimate"] == 2
    assert set(page["items"][0]) == {
        "id",
        "ts_start",
        "duration_ms",
        "route",
        "method",
        "status",
        "model",
        "route_reason",
        "queue_ms",
        "forward_ms",
        "client_key_id",
        "span_count",
        "error_code",
    }
    assert page["items"][0] == {
        "id": "aa000001",
        "ts_start": NOW - 1,
        "duration_ms": 100.0,
        "route": "/predict",
        "method": "POST",
        "status": 200,
        "model": "english",
        "route_reason": "state is English",
        "queue_ms": 5.0,
        "forward_ms": 80.0,
        "client_key_id": "k_3f8a",
        "span_count": 3,
        "error_code": None,
    }
    assert page["items"][1]["span_count"] == 0


# ---------------------------------------------------------------------------
# trace detail
# ---------------------------------------------------------------------------


def test_trace_detail_summary_math_and_ordering(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(
        conn,
        "9f2c1a4b",
        NOW - 5,
        duration=412.5,
        forward_ms=391.4,
        queue_ms=5.2,
        route_reason="state is English",
        client_key_id="k_3f8a",
        tags='["seed"]',
        meta='{"sampled": true}',
    )
    # inserted out of order on purpose: the waterfall order is start_ms, then id
    _insert_obs(conn, "o3", "9f2c1a4b", 50.0)
    _insert_obs(conn, "o1", "9f2c1a4b", 10.0)
    _insert_obs(conn, "o2", "9f2c1a4b", 30.0, meta='{"depth": 2}')
    _insert_score(conn, "s2", "9f2c1a4b", "quality", 4.0, 2.0)
    _insert_score(conn, "s1", "9f2c1a4b", "helpful", 1.0, 1.0)
    _insert_log(conn, NOW - 300, trace_id="9f2c1a4b", message="first")
    _insert_log(conn, NOW - 200, trace_id="9f2c1a4b", message="second")
    _insert_log(conn, NOW - 100, trace_id="9f2c1a4b", message="third")
    _insert_log(conn, NOW - 50, trace_id="other000", message="unrelated")

    detail = trace_detail(conn, "9f2c1a4b")
    assert detail is not None

    trace = detail["trace"]
    assert set(trace) == {
        "id",
        "ts_start",
        "duration_ms",
        "route",
        "method",
        "status",
        "model",
        "route_reason",
        "lang",
        "queue_ms",
        "forward_ms",
        "state_bytes",
        "question_count",
        "client_key_id",
        "session_id",
        "error_code",
        "error_message",
        "tags",
        "meta",
    }
    assert trace["tags"] == ["seed"]
    assert trace["meta"] == {"sampled": True}
    assert trace["duration_ms"] == 412.5

    assert [o["id"] for o in detail["observations"]] == ["o1", "o2", "o3"]
    assert [o["start_ms"] for o in detail["observations"]] == [10.0, 30.0, 50.0]
    assert detail["observations"][1]["meta"] == {"depth": 2}

    assert [s["id"] for s in detail["scores"]] == ["s1", "s2"]
    assert [log["message"] for log in detail["logs"]] == ["third", "second", "first"]

    # hand-computed: framework = 412.5 - 391.4 - 5.2 = 15.9;
    # forward_share = 391.4/412.5 = 0.9488..., queue_share = 5.2/412.5 = 0.0126...
    assert detail["summary"] == {
        "framework_ms": 15.9,
        "forward_ms": 391.4,
        "forward_share": 0.9488,
        "queue_share": 0.0126,
        "span_count": 3,
        "status_class": "2xx",
    }


def test_trace_detail_missing_trace_returns_none(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    counting = _CountingConn(conn)
    assert trace_detail(counting, "00000000") is None
    assert counting.executes == 1


def test_trace_detail_runs_exactly_one_query_per_table(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "9f2c1a4b", NOW - 5, duration=412.5, forward_ms=391.4, queue_ms=5.2)
    _insert_obs(conn, "o1", "9f2c1a4b", 10.0)
    _insert_obs(conn, "o2", "9f2c1a4b", 30.0)
    _insert_score(conn, "s1", "9f2c1a4b", "helpful", 1.0, 1.0)
    _insert_log(conn, NOW - 100, trace_id="9f2c1a4b")

    counting = _CountingConn(conn)
    detail = trace_detail(counting, "9f2c1a4b")
    assert detail is not None
    # traces + observations + scores + log_entry: one query per table, no N+1
    assert counting.executes == 4


def test_trace_detail_logs_are_bounded_and_newest_first(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _insert_trace(conn, "bbbbbbbb", NOW - 5)
    for index in range(1, DETAIL_LOG_LIMIT + 6):
        _insert_log(conn, NOW - index, trace_id="bbbbbbbb", message=f"m{index}")

    detail = trace_detail(conn, "bbbbbbbb")
    assert detail is not None
    logs = detail["logs"]
    assert len(logs) == DETAIL_LOG_LIMIT
    assert logs[0]["message"] == "m1"  # newest kept, oldest dropped
    assert logs[-1]["message"] == f"m{DETAIL_LOG_LIMIT}"


# ---------------------------------------------------------------------------
# metrics series
# ---------------------------------------------------------------------------


def test_step_for_range_thresholds() -> None:
    assert step_for_range(900) == 10  # 15 m
    assert step_for_range(901) == 60
    assert step_for_range(21600) == 60  # 6 h
    assert step_for_range(21601) == 3600
    assert step_for_range(604800) == 3600  # 7 d


def test_metrics_series_values_and_child_merge(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _roll(conn, NOW, "requests", "english", "/predict", counter_stats(38))
    _roll(conn, NOW, "errors", "", "", counter_stats(2), status=500)
    _roll(conn, NOW, "latency", "english", "/predict", summarize([100.0, 200.0, 300.0]))
    _roll(conn, NOW, "queue", "english", "/predict", summarize([5.0, 15.0]))
    # second bucket holds two models: counts add, percentiles merge child-weighted
    _roll(conn, NOW + 10, "requests", "english", "/predict", counter_stats(38))
    _roll(conn, NOW + 10, "requests", "multilingual", "/route", counter_stats(7))
    _roll(conn, NOW + 10, "latency", "english", "/predict", summarize([100.0, 200.0, 300.0]))
    _roll(conn, NOW + 10, "latency", "multilingual", "/route", summarize([100.0, 300.0]))
    _roll(conn, NOW + 10, "queue", "english", "/route", summarize([5.0, 15.0]))
    _roll(conn, NOW + 10, "queue", "multilingual", "/route", summarize([1.0, 2.0, 3.0]))

    result = metrics_series(conn, "requests,latency,errors,queue", NOW, NOW + 30, 10)
    assert set(result) == {"step", "window", "series", "note"}
    assert result["step"] == 10
    assert result["window"] == {"since": NOW, "until": NOW + 30}
    assert result["note"] == "percentiles are bucket-level"
    assert [entry["metric"] for entry in result["series"]] == [
        "requests",
        "latency",
        "errors",
        "queue",
    ]

    series = {entry["metric"]: entry["points"] for entry in result["series"]}
    # requests = per-second rate: 38/10 = 3.8, (38+7)/10 = 4.5, empty bucket = 0.0
    assert series["requests"] == [[NOW, 3.8], [NOW + 10, 4.5], [NOW + 20, 0.0]]
    assert series["errors"] == [[NOW, 2], [NOW + 10, 0], [NOW + 20, 0]]
    # nearest-rank on [100,200,300]: p50 = 200, p90/p95/p99 = 300
    assert series["latency"][0] == [NOW, {"p50": 200.0, "p90": 300.0, "p95": 300.0,
                                         "p99": 300.0}]
    # child-weighted merge: (200*3 + 100*2)/5 = 160
    assert series["latency"][1] == [NOW + 10, {"p50": 160.0, "p90": 300.0, "p95": 300.0,
                                               "p99": 300.0}]
    # queue = bucket average: 20/2 = 10, (20+6)/5 = 5.2
    assert series["queue"] == [[NOW, 10.0], [NOW + 10, 5.2], [NOW + 20, 0.0]]


def test_metrics_series_empty_window_emits_every_bucket_as_zeros(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    result = metrics_series(conn, "requests,errors,latency,queue", NOW, NOW + 30, 10)
    for entry in result["series"]:
        points = entry["points"]
        assert [point[0] for point in points] == [NOW, NOW + 10, NOW + 20]  # no gaps
        if entry["metric"] == "latency":
            assert all(
                point[1] == {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
                for point in points
            )
        elif entry["metric"] == "errors":
            assert all(point[1] == 0 for point in points)
        else:
            assert all(point[1] == 0.0 for point in points)


def test_metrics_series_aligns_buckets_to_the_step_grid(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    result = metrics_series(conn, "requests", NOW + 3, NOW + 33, 10)
    assert result["window"] == {"since": NOW + 3, "until": NOW + 33}
    # buckets snap down to the 10 s grid so the whole window stays covered, no gaps
    assert [point[0] for point in result["series"][0]["points"]] == [
        NOW,
        NOW + 10,
        NOW + 20,
        NOW + 30,
    ]


def test_metrics_series_model_and_route_filters(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _roll(conn, NOW, "requests", "english", "/predict", counter_stats(38))
    _roll(conn, NOW, "requests", "multilingual", "/route", counter_stats(7))
    _roll(conn, NOW, "errors", "", "", counter_stats(2), status=500)

    english = metrics_series(conn, "requests,errors", NOW, NOW + 10, 10, model="english")
    assert english["series"][0]["points"] == [[NOW, 3.8]]
    # errors rollup rows carry no model dimension, so they stay unfiltered (documented)
    assert english["series"][1]["points"] == [[NOW, 2]]

    routed = metrics_series(conn, "requests", NOW, NOW + 10, 10, route="/route")
    assert routed["series"][0]["points"] == [[NOW, 0.7]]


def test_metrics_series_rejects_invalid_arguments(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    with pytest.raises(InvalidFilter, match="step"):
        metrics_series(conn, "requests", NOW, NOW + 10, 30)
    with pytest.raises(InvalidFilter, match="step"):
        metrics_series(conn, "requests", NOW, NOW + 10, "abc")
    with pytest.raises(InvalidFilter, match="metrics"):
        metrics_series(conn, "", NOW, NOW + 10, 10)
    with pytest.raises(InvalidFilter, match="metrics"):
        metrics_series(conn, "requests,bogus", NOW, NOW + 10, 10)
    with pytest.raises(InvalidFilter, match="since"):
        metrics_series(conn, "requests", NOW + 10, NOW, 10)
    assert parse_metrics("requests,latency,requests") == ["requests", "latency"]


# ---------------------------------------------------------------------------
# metrics summary
# ---------------------------------------------------------------------------


def _seed_summary_fixture(conn: Any) -> None:
    # current window [NOW-300, NOW): durations [50,100,200,300,400], queues sum 51
    _insert_trace(conn, "cc000001", NOW - 10, duration=100.0, status=200, queue_ms=5.0)
    _insert_trace(conn, "cc000002", NOW - 20, duration=200.0, status=422, queue_ms=10.0)
    _insert_trace(conn, "cc000003", NOW - 30, duration=300.0, status=500, queue_ms=15.0)
    _insert_trace(conn, "cc000004", NOW - 40, duration=400.0, status=400, queue_ms=20.0)
    _insert_trace(conn, "cc000005", NOW - 50, duration=50.0, status=200, queue_ms=1.0)
    # previous window [NOW-600, NOW-300): durations [500, 600], queues sum 30
    _insert_trace(conn, "cc000006", NOW - 310, duration=500.0, status=200, queue_ms=0.0)
    _insert_trace(conn, "cc000007", NOW - 320, duration=600.0, status=500, queue_ms=30.0)
    # older than both windows and of the 5-minute throttle window
    _insert_trace(conn, "cc000008", NOW - 1000, duration=999.0, status=429, queue_ms=0.0)


def test_metrics_summary_deltas_are_hand_computed(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_summary_fixture(conn)

    result = metrics_summary(conn, 300.0, now=NOW)
    assert set(result) == {
        "range_s",
        "window",
        "prev_window",
        "requests_per_s",
        "requests_per_s_prev",
        "errors",
        "errors_prev",
        "errors_by_status",
        "p50",
        "p50_prev",
        "p95",
        "p95_prev",
        "queue_ms",
        "queue_ms_prev",
        "deltas",
        "total_requests",
    }  # throttled_5m is absent while the count is zero
    assert result["window"] == {"since": NOW - 300, "until": NOW}
    assert result["prev_window"] == {"since": NOW - 600, "until": NOW - 300}

    assert result["requests_per_s"] == pytest.approx(5 / 300)
    assert result["requests_per_s_prev"] == pytest.approx(2 / 300)
    # current statuses 400, 422, 500; previous has only 500
    assert result["errors"] == 3
    assert result["errors_prev"] == 1
    assert result["errors_by_status"] == {"400": 1, "422": 1, "500": 1}
    # nearest-rank: [50,100,200,300,400] -> p50 = 200, p95 = 400; [500,600] -> 500, 600
    assert result["p50"] == 200.0
    assert result["p50_prev"] == 500.0
    assert result["p95"] == 400.0
    assert result["p95_prev"] == 600.0
    assert result["queue_ms"] == pytest.approx(10.2)  # 51/5
    assert result["queue_ms_prev"] == pytest.approx(15.0)  # 30/2

    assert result["deltas"]["requests_per_s"] == pytest.approx(3 / 300)
    assert result["deltas"]["errors"] == 2
    assert result["deltas"]["p50"] == -300.0
    assert result["deltas"]["p95"] == -200.0
    assert result["deltas"]["queue_ms"] == pytest.approx(-4.8)
    assert result["total_requests"] == 8


def test_metrics_summary_throttled_cell_is_conditional(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_summary_fixture(conn)

    # the only 429 sits outside the last five minutes: no cell, no field
    assert "throttled_5m" not in metrics_summary(conn, 300.0, now=NOW)

    # a 429 between 5 and 10 minutes back is still too old
    _insert_trace(conn, "cc000009", NOW - 400, status=429, duration=1.0)
    assert "throttled_5m" not in metrics_summary(conn, 300.0, now=NOW)

    # a 429 inside the last five minutes surfaces the count
    _insert_trace(conn, "cc000010", NOW - 60, status=429, duration=1.0)
    assert metrics_summary(conn, 300.0, now=NOW)["throttled_5m"] == 1


def test_metrics_summary_rejects_bad_range(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    with pytest.raises(InvalidFilter, match="range"):
        metrics_summary(conn, 0, now=NOW)
    with pytest.raises(InvalidFilter, match="range"):
        metrics_summary(conn, -300.0, now=NOW)
    with pytest.raises(InvalidFilter, match="range"):
        metrics_summary(conn, "soon", now=NOW)


# ---------------------------------------------------------------------------
# metrics models
# ---------------------------------------------------------------------------


def test_metrics_models_mix_sums_equal_window_total(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    bucket1, bucket2 = NOW - 60, NOW - 50
    _roll(conn, bucket1, "requests", "english", "/predict", counter_stats(3))
    _roll(conn, bucket1, "requests", "multilingual", "/predict", counter_stats(1))
    _roll(conn, bucket1, "requests", "", "/route", counter_stats(9))  # model-less: excluded
    _roll(conn, bucket2, "requests", "english", "/predict", counter_stats(2))
    # other metrics must not leak into the mix
    _roll(conn, bucket1, "latency", "english", "/predict", summarize([1.0, 2.0]))
    _roll(conn, bucket1, "errors", "", "", counter_stats(4), status=500)

    # matching traces so the window total can be cross-checked against storage
    for index in range(3):
        _insert_trace(conn, f"dd00000{index}", NOW - 59 + index, model="english")
    _insert_trace(conn, "dd000010", NOW - 52, model="multilingual")
    _insert_trace(conn, "dd000011", NOW - 49, model="english")
    _insert_trace(conn, "dd000012", NOW - 48, model="english")
    _insert_trace(conn, "dd000013", NOW - 51, model=None)  # model-less, excluded
    _insert_trace(conn, "dd000014", NOW - 1000, model="english")  # outside the window

    result = metrics_models(conn, NOW - 60, NOW + 40, now=NOW)
    assert result["step"] == 10
    assert result["window"] == {"since": NOW - 60, "until": NOW + 40}
    assert result["models"] == ["english", "multilingual"]
    assert result["total"] == 6
    summed = sum(sum(entry["counts"].values()) for entry in result["buckets"])
    assert summed == result["total"] == 6
    # the window total also equals the stored model-attributed traces in the window
    stored = conn.execute(
        "SELECT COUNT(*) FROM traces WHERE model IS NOT NULL"
        " AND ts_start >= ? AND ts_start < ?",
        (NOW - 60, NOW + 40),
    ).fetchone()[0]
    assert stored == result["total"]
    assert len(result["buckets"]) == 10  # every bucket emitted, gaps included
    assert result["buckets"][0] == {"ts": bucket1, "counts": {"english": 3,
                                                              "multilingual": 1}}
    assert result["buckets"][2] == {"ts": NOW - 40, "counts": {}}


def test_metrics_models_defaults_to_15m_and_validates_window(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    result = metrics_models(conn, None, None, now=NOW)
    assert result["window"] == {"since": NOW - 900, "until": NOW}
    assert result["step"] == 10
    assert result["total"] == 0
    with pytest.raises(InvalidFilter, match="since"):
        metrics_models(conn, NOW, NOW - 1, now=NOW)


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


def _seed_logs(conn: Any) -> None:
    _insert_log(conn, NOW - 10, trace_id="aa000001", message="request done")
    _insert_log(conn, NOW - 20, level="error", trace_id="aa000001",
                message="boom 100% failed")
    _insert_log(conn, NOW - 30, level="warning", message="slow warning")
    _insert_log(conn, NOW - 40, level="debug", trace_id="bb000002", message="verbose")


def test_logs_filter_matrix(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_logs(conn)

    assert [i["message"] for i in logs_list(conn, {"level": "error"})["items"]] == [
        "boom 100% failed"
    ]
    assert [i["message"] for i in logs_list(conn, {"level": "warning"})["items"]] == [
        "slow warning"
    ]
    # documented alias: the API accepts "warn" for the stored level "warning"
    assert [i["message"] for i in logs_list(conn, {"level": "warn"})["items"]] == [
        "slow warning"
    ]
    assert [i["message"] for i in logs_list(conn, {"q": "boom"})["items"]] == [
        "boom 100% failed"
    ]
    assert [i["message"] for i in logs_list(conn, {"q": "%"})["items"]] == [
        "boom 100% failed"
    ]
    assert [i["message"] for i in logs_list(conn, {"trace_id": "aa000001"})["items"]] == [
        "request done",
        "boom 100% failed",
    ]
    assert len(logs_list(conn, {"since": str(NOW - 25)})["items"]) == 2
    assert len(logs_list(conn, {"until": str(NOW - 25)})["items"]) == 2
    # newest first by default
    assert [i["message"] for i in logs_list(conn, {})["items"]] == [
        "request done",
        "boom 100% failed",
        "slow warning",
        "verbose",
    ]


def test_logs_invalid_filters_name_the_parameter(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_logs(conn)
    with pytest.raises(InvalidFilter, match="level"):
        logs_list(conn, {"level": "fatal"})
    with pytest.raises(InvalidFilter, match="limit"):
        logs_list(conn, {"limit": "201"})
    with pytest.raises(InvalidFilter, match="cursor"):
        logs_list(conn, {"cursor": "@@@"})
    with pytest.raises(InvalidFilter, match="bogus"):
        logs_list(conn, {"bogus": "1"})
    with pytest.raises(InvalidFilter, match="range"):
        logs_list(conn, {"range": "15m", "since": "100"})


def test_logs_range_uses_wall_clock(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    fresh = time.time()
    inside = _insert_log(conn, fresh - 30, message="fresh")
    _insert_log(conn, fresh - 7000, message="stale")
    page = logs_list(conn, {"range": "15m"})
    assert [i["id"] for i in page["items"]] == [inside]


def test_logs_envelope_shape_and_cursor_stability(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_logs(conn)

    counting = _CountingConn(conn)
    page = logs_list(counting, {"limit": "2"})
    assert counting.executes == 2  # page query + filtered total count
    assert set(page) == {"items", "next_cursor", "total_estimate"}
    assert page["total_estimate"] == 4
    assert set(page["items"][0]) == {"id", "ts", "level", "source", "trace_id", "message"}
    assert [i["message"] for i in page["items"]] == ["request done", "boom 100% failed"]
    cursor = page["next_cursor"]
    assert isinstance(cursor, str)

    _insert_log(conn, NOW - 5, message="even newer")  # concurrent insert

    page2 = logs_list(conn, {"limit": "2", "cursor": cursor})
    assert [i["message"] for i in page2["items"]] == ["slow warning", "verbose"]
    assert page2["total_estimate"] == 5
    assert page2["next_cursor"] is None


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def test_insert_audit_row_shape(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    before = time.time()
    insert_audit(conn, "owner@example.com", "key.created", target="k_abc",
                 meta={"name": "ci"})
    insert_audit(conn, "system", "settings.updated")

    rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    assert len(rows) == 2

    first, second = rows
    assert first["actor"] == "owner@example.com"
    assert first["actor_id"] is None  # Phase 3 owns real identities
    assert first["action"] == "key.created"
    assert first["target"] == "k_abc"
    assert first["result"] == "ok"
    assert json.loads(first["meta"]) == {"name": "ci"}
    assert before <= first["ts"] <= time.time()

    assert second["actor"] == "system"
    assert second["action"] == "settings.updated"
    assert second["target"] is None
    assert second["result"] == "ok"
    assert second["meta"] is None
