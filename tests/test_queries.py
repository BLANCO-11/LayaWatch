"""queries tests: insert round-trips, rollup upsert/merge, read helpers against fixtures."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from layawatch.obs.recorder import Observation, Score, Trace
from layawatch.obs.rollup import summarize
from layawatch.store import db
from layawatch.store.queries import (
    count_traces,
    counter_stats,
    insert_observations,
    insert_scores,
    insert_trace,
    load_rollup_row,
    merge_stats,
    newest_trace,
    observations_for,
    rollup_row,
    upsert_rollup,
)


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "queries.sqlite3")
    db.migrate(conn)
    return conn


def _trace(trace_id: str, ts: float) -> Trace:
    return Trace(
        id=trace_id,
        ts_start=ts,
        duration_ms=12.5,
        route="/predict",
        method="POST",
        status=200,
        model="english",
        queue_ms=1.5,
        tags=["playground", "nightly"],
        meta={"sampled": True, "device": "cpu"},
    )


def _obs(obs_id: str, trace_id: str, start_ms: float) -> Observation:
    return Observation(
        id=obs_id,
        trace_id=trace_id,
        parent_id=None,
        name="http.receive",
        type="span",
        start_ms=start_ms,
        duration_ms=4.0,
        status="ok",
        model=None,
        input=None,
        output=None,
        meta={},
    )


def test_insert_trace_round_trips_json_columns(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    insert_trace(conn, [_trace("0add1e57", 1700000000.0)])
    conn.commit()
    assert count_traces(conn) == 1
    row = newest_trace(conn)
    assert row is not None
    assert row["id"] == "0add1e57"
    assert row["route"] == "/predict"
    assert row["status"] == 200
    assert row["duration_ms"] == 12.5
    assert row["queue_ms"] == 1.5
    assert json.loads(row["tags"]) == ["playground", "nightly"]
    assert json.loads(row["meta"]) == {"sampled": True, "device": "cpu"}
    conn.close()


def test_newest_trace_prefers_later_start_and_none_when_empty(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    assert newest_trace(conn) is None  # empty table
    insert_trace(conn, [_trace("aaaa0001", 200.0)])
    insert_trace(conn, [_trace("bbbb0002", 100.0)])  # inserted later, started earlier
    assert newest_trace(conn)["id"] == "aaaa0001"
    # equal ts_start falls back to insertion order (rowid)
    insert_trace(conn, [_trace("cccc0003", 200.0)])
    assert newest_trace(conn)["id"] == "cccc0003"
    conn.close()


def test_count_traces_tracks_inserts_and_deletes(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    assert count_traces(conn) == 0
    insert_trace(conn, [_trace("a1111111", 1.0), _trace("a2222222", 2.0),
                        _trace("a3333333", 3.0)])
    conn.commit()
    assert count_traces(conn) == 3
    conn.execute("DELETE FROM traces WHERE id = 'a1111111'")
    conn.commit()
    assert count_traces(conn) == 2
    conn.close()


def test_observations_for_orders_by_start_and_isolates_traces(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    insert_trace(conn, [_trace("ffff0001", 1.0), _trace("ffff0002", 2.0)])
    conn.commit()
    # inserted out of order on purpose; the helper returns waterfall order
    insert_observations(
        conn,
        [
            _obs("ob000020", "ffff0001", start_ms=20.0),
            _obs("ob000000", "ffff0001", start_ms=0.0),
            _obs("ob000010", "ffff0001", start_ms=10.0),
            _obs("ob000099", "ffff0002", start_ms=5.0),
        ],
    )
    conn.commit()
    rows = observations_for(conn, "ffff0001")
    assert [row["id"] for row in rows] == ["ob000000", "ob000010", "ob000020"]
    assert [row["start_ms"] for row in rows] == [0.0, 10.0, 20.0]
    assert [row["id"] for row in observations_for(conn, "ffff0002")] == ["ob000099"]
    assert observations_for(conn, "missing1") == []
    conn.close()


def test_insert_scores_round_trips(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    insert_trace(conn, [_trace("bad0f00d", 1.0)])
    conn.commit()
    score = Score(
        id="sc000001",
        trace_id="bad0f00d",
        name="quality",
        value=4.0,
        data_type="numeric",
        source="api",
        comment="solid",
        ts=1700000000.5,
    )
    insert_scores(conn, [score])
    conn.commit()
    row = conn.execute("SELECT * FROM scores WHERE id = 'sc000001'").fetchone()
    assert row is not None
    assert (
        row["trace_id"], row["name"], row["value"], row["data_type"], row["source"],
        row["comment"], row["ts"],
    ) == ("bad0f00d", "quality", 4.0, "numeric", "api", "solid", 1700000000.5)
    conn.close()


def test_empty_insert_sequences_are_noops(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    insert_trace(conn, [])
    insert_observations(conn, [])
    insert_scores(conn, [])
    conn.commit()
    assert count_traces(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
    conn.close()


def test_upsert_rollup_inserts_then_replaces_with_a_merge(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    key = (1700000000, 60, "latency", "english", "/predict", 0)
    assert load_rollup_row(conn, *key) is None

    first = rollup_row(*key, summarize([10.0, 20.0, 30.0]))
    upsert_rollup(conn, first)
    row = load_rollup_row(conn, *key)
    assert row is not None
    assert (row["count"], row["sum"], row["min"], row["max"]) == (3, 60.0, 10.0, 30.0)
    assert row["p50"] == 20.0

    # the writer's second close of the same bucket merges the new increment in
    merged = merge_stats("latency", row, summarize([40.0]))
    upsert_rollup(conn, rollup_row(*key, merged))
    row = load_rollup_row(conn, *key)
    assert (row["count"], row["sum"], row["min"], row["max"]) == (4, 100.0, 10.0, 40.0)
    assert row["p50"] == (20.0 * 3 + 40.0) / 4  # child-count-weighted: 25.0
    conn.close()


def test_counter_stats_merge_exactly(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    three = counter_stats(3)
    assert three == {
        "count": 3, "sum": 3.0, "min": 1.0, "max": 1.0,
        "p50": None, "p90": None, "p95": None, "p99": None,
    }
    assert merge_stats("requests", None, three) == three
    merged = merge_stats("requests", three, counter_stats(2))
    assert (merged["count"], merged["sum"], merged["min"], merged["max"]) == (5, 5.0, 1.0, 1.0)
    assert merged["p50"] is None and merged["p99"] is None

    # persisting a counter row round-trips the NULL percentiles
    key = (1700000010, 10, "requests", "english", "/predict", 0)
    upsert_rollup(conn, rollup_row(*key, merged))
    row = load_rollup_row(conn, *key)
    assert row is not None
    assert (row["count"], row["sum"]) == (5, 5.0)
    assert row["p50"] is None
    conn.close()


def test_merge_stats_reads_existing_rows_with_null_percentiles(tmp_path: Path) -> None:
    # defensive path: a counter row (NULL percentiles) merged as a distribution must not crash
    conn = _conn(tmp_path)
    key = (1700000020, 60, "errors", "", "", 500)
    upsert_rollup(conn, rollup_row(*key, counter_stats(2)))
    row = load_rollup_row(conn, *key)
    merged = merge_stats("requests", row, counter_stats(3))
    assert (merged["count"], merged["sum"]) == (5, 5.0)
    assert merged["p50"] is None
    conn.close()


def test_merge_stats_distribution_tolerates_null_percentiles(tmp_path: Path) -> None:
    # a partial/legacy distribution row can carry NULL percentiles; merging must not crash
    conn = _conn(tmp_path)
    key = (1700000030, 60, "latency", "english", "/predict", 0)
    upsert_rollup(
        conn,
        rollup_row(*key, {
            "count": 2, "sum": 40.0, "min": 5.0, "max": 35.0,
            "p50": None, "p90": None, "p95": None, "p99": None,
        }),
    )
    row = load_rollup_row(conn, *key)
    merged = merge_stats("latency", row, summarize([10.0]))
    assert (merged["count"], merged["sum"], merged["min"], merged["max"]) == (3, 50.0, 5.0, 35.0)
    assert merged["p50"] == pytest.approx((0.0 * 2 + 10.0 * 1) / 3)
    conn.close()
