"""Metrics API contract: allowlisted parameters, server-chosen steps, verbatim shapes."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from layawatch.api.metrics import add_metrics_routes
from layawatch.http.router import Router
from layawatch.http.types import Request, Response
from layawatch.store import db
from layawatch.store.queries import counter_stats, rollup_row, upsert_rollup

#: Frozen, 10 s-aligned epoch: every window below is hand-derived from it.
NOW = 1_790_000_000.0


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", lambda: NOW)


def _build(tmp_path: Path) -> tuple[Router, Any]:
    db_path = tmp_path / "state.sqlite3"
    conn = db.connect(db_path)
    db.migrate(conn)
    router = Router()
    add_metrics_routes(router, db_path)
    return router, conn


def call(router: Router, target: str) -> Response:
    return router.dispatch(Request.build("GET", target, {}, b"", "feedbeef", "127.0.0.1"))


def error_of(response: Response) -> dict:
    return json.loads(response.body)["error"]


def _insert_trace(
    conn: Any, trace_id: str, ts: float, *, duration: float, status: int, queue: float
) -> None:
    conn.execute(
        "INSERT INTO traces (id, ts_start, duration_ms, route, method, status, queue_ms)"
        " VALUES (?,?,?,?,?,?,?)",
        (trace_id, ts, duration, "/predict", "POST", status, queue),
    )


def _roll(conn: Any, bucket: int, model: str, count: int) -> None:
    upsert_rollup(
        conn, rollup_row(bucket, 10, "requests", model, "/predict", 0, counter_stats(count))
    )


# ---------------------------------------------------------------------------
# parameter validation: every rejection names the offending parameter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "named"),
    [
        ("/api/v1/metrics?bogus=1", "bogus"),
        ("/api/v1/metrics?metrics=bogus_name", "bogus_name"),
        ("/api/v1/metrics?range=15m", "metrics"),
        ("/api/v1/metrics?metrics=requests&step=30", "step"),
        ("/api/v1/metrics?metrics=requests&range=9h", "range"),
        ("/api/v1/metrics?metrics=requests&range=15m&since=100", "since"),
        ("/api/v1/metrics/summary?bogus=1", "bogus"),
        ("/api/v1/metrics/summary?range=9h", "range"),
        ("/api/v1/metrics/models?bogus=1", "bogus"),
        ("/api/v1/metrics/models?range=9h", "range"),
    ],
)
def test_invalid_parameters_are_400_invalid_filter_naming_the_parameter(
    tmp_path: Path, target: str, named: str
) -> None:
    router, _conn = _build(tmp_path)
    response = call(router, target)
    assert response.status == 400
    error = error_of(response)
    assert error["code"] == "invalid_filter"
    assert named in error["message"]


# ---------------------------------------------------------------------------
# series
# ---------------------------------------------------------------------------


def test_range_15m_is_ninety_gapless_buckets_with_zeros_when_empty(tmp_path: Path) -> None:
    router, _conn = _build(tmp_path)
    response = call(router, "/api/v1/metrics?metrics=requests,latency,errors,queue&range=15m")
    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["step"] == 10
    assert payload["window"] == {"since": NOW - 900, "until": NOW}
    assert payload["note"] == "percentiles are bucket-level"
    assert [series["metric"] for series in payload["series"]] == [
        "requests",
        "latency",
        "errors",
        "queue",
    ]
    for series in payload["series"]:
        points = series["points"]
        stamps = [point[0] for point in points]
        assert stamps == list(range(int(NOW - 900), int(NOW), 10))  # no gaps, in order
        for _ts, value in points[1:]:
            assert value == points[0][1]  # empty window: every bucket equally zero
    by_metric = {series["metric"]: series["points"] for series in payload["series"]}
    assert all(value == 0.0 for _ts, value in by_metric["requests"])
    assert all(value == 0 for _ts, value in by_metric["errors"])
    assert all(value == 0.0 for _ts, value in by_metric["queue"])
    for _ts, value in by_metric["latency"]:
        assert value == {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}


@pytest.mark.parametrize(
    ("range_token", "step", "buckets"),
    [
        # docs/observability-model.md 6: <= 15 m -> 10 s, <= 6 h -> 1 m, beyond -> 1 h.
        ("15m", 10, 90),
        ("1h", 60, 61),
        ("6h", 60, 361),
        ("7d", 3600, 169),
    ],
)
def test_server_chosen_step_follows_the_window(tmp_path: Path, range_token, step, buckets) -> None:
    router, _conn = _build(tmp_path)
    payload = json.loads(
        call(router, f"/api/v1/metrics?metrics=requests&range={range_token}").body
    )
    assert payload["step"] == step
    assert len(payload["series"][0]["points"]) == buckets
    stamps = [point[0] for point in payload["series"][0]["points"]]
    assert stamps == list(range(stamps[0], stamps[-1] + step, step))  # contiguous


def test_explicit_allowed_step_is_honored(tmp_path: Path) -> None:
    router, _conn = _build(tmp_path)
    payload = json.loads(
        call(router, "/api/v1/metrics?metrics=requests&range=15m&step=60").body
    )
    assert payload["step"] == 60
    assert len(payload["series"][0]["points"]) == 16  # 920 s of step-aligned buckets


def test_latency_points_carry_all_four_percentile_keys(tmp_path: Path) -> None:
    router, conn = _build(tmp_path)
    stats = {
        "count": 4,
        "sum": 1600.0,
        "min": 120.0,
        "max": 900.0,
        "p50": 350.0,
        "p90": 700.0,
        "p95": 850.0,
        "p99": 950.0,
    }
    upsert_rollup(conn, rollup_row(int(NOW - 10), 10, "latency", "english", "/predict", 0, stats))
    conn.commit()
    payload = json.loads(call(router, "/api/v1/metrics?metrics=latency&range=15m").body)
    points = payload["series"][0]["points"]
    assert points[-1][0] == NOW - 10
    seeded = points[-1][1]
    assert set(seeded) == {"p50", "p90", "p95", "p99"}
    assert seeded == {"p50": 350.0, "p90": 700.0, "p95": 850.0, "p99": 950.0}
    assert all(
        value == {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0} for _ts, value in points[:-1]
    )


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_summary_defaults_to_15m_with_hand_computed_deltas(tmp_path: Path) -> None:
    router, conn = _build(tmp_path)
    # current window [NOW-900, NOW): durations [100, 300], queues sum 20, one 500.
    _insert_trace(conn, "aa000001", NOW - 10, duration=100.0, status=200, queue=0.0)
    _insert_trace(conn, "aa000002", NOW - 20, duration=300.0, status=500, queue=20.0)
    # previous window [NOW-1800, NOW-900): one 400 ms trace, queue 10.
    _insert_trace(conn, "bb000001", NOW - 950, duration=400.0, status=200, queue=10.0)
    conn.commit()
    response = call(router, "/api/v1/metrics/summary")
    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["range_s"] == 900.0
    assert payload["window"] == {"since": NOW - 900, "until": NOW}
    assert payload["prev_window"] == {"since": NOW - 1800, "until": NOW - 900}
    assert payload["requests_per_s"] == 2 / 900
    assert payload["requests_per_s_prev"] == 1 / 900
    assert payload["errors"] == 1
    assert payload["errors_prev"] == 0
    assert payload["errors_by_status"] == {"500": 1}
    assert payload["p50"] == 100.0  # nearest-rank over [100, 300]
    assert payload["p50_prev"] == 400.0
    assert payload["p95"] == 300.0
    assert payload["queue_ms"] == 10.0
    assert payload["queue_ms_prev"] == 10.0
    assert payload["total_requests"] == 3
    assert payload["deltas"] == {
        "requests_per_s": 2 / 900 - 1 / 900,
        "errors": 1,
        "p50": -300.0,
        "p95": -100.0,
        "queue_ms": 0.0,
    }


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_model_mix_totals_match_the_window(tmp_path: Path) -> None:
    router, conn = _build(tmp_path)
    _roll(conn, int(NOW - 20), "english", 3)
    _roll(conn, int(NOW - 10), "multilingual", 2)
    _roll(conn, int(NOW - 10), "", 9)  # model-less rows never reach the legend
    conn.commit()
    response = call(router, "/api/v1/metrics/models?range=15m")
    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["step"] == 10
    assert payload["window"] == {"since": NOW - 900, "until": NOW}
    assert payload["models"] == ["english", "multilingual"]
    assert payload["total"] == 5
    assert len(payload["buckets"]) == 90
    counted = sum(sum(bucket["counts"].values()) for bucket in payload["buckets"])
    assert counted == payload["total"]
    assert payload["buckets"][88] == {"ts": NOW - 20, "counts": {"english": 3}}
    assert payload["buckets"][89] == {"ts": NOW - 10, "counts": {"multilingual": 2}}
    default = call(router, "/api/v1/metrics/models")
    assert default.body == response.body  # omitted range means the same 15 m window
