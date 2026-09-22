"""Writer tests: batching triggers, transaction atomicity, backpressure, rollup math."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

import layawatch.store.writer as writer_module
from layawatch.obs.recorder import Observation, Trace
from layawatch.store import db
from layawatch.store.queries import count_traces, load_rollup_row, observations_for
from layawatch.store.writer import Writer


def _make_db(tmp_path: Path) -> str:
    path = tmp_path / "state.sqlite3"
    conn = db.connect(path)
    db.migrate(conn)
    conn.close()
    return str(path)


def _start(tmp_path: Path, **kwargs: object) -> tuple[Writer, list, str]:
    path = _make_db(tmp_path)
    flushed: list = []
    writer = Writer(path, on_flush=flushed.append, **kwargs)
    writer.start()
    return writer, flushed, path


def _obs(obs_id: str, trace_id: str, start_ms: float = 0.0) -> Observation:
    return Observation(
        id=obs_id,
        trace_id=trace_id,
        parent_id=None,
        name="http.receive",
        type="span",
        start_ms=start_ms,
        duration_ms=1.0,
        status="ok",
        model=None,
        input=None,
        output=None,
        meta={},
    )


def _trace(
    trace_id: str,
    ts: float,
    *,
    duration_ms: float,
    queue_ms: float = 0.0,
    status: int = 200,
    model: str | None = "english",
    route: str = "/predict",
    sampled: bool = True,
    error_code: str | None = None,
    observations: tuple[Observation, ...] = (),
) -> Trace:
    return Trace(
        id=trace_id,
        ts_start=ts,
        duration_ms=duration_ms,
        route=route,
        method="POST",
        status=status,
        model=model,
        queue_ms=queue_ms,
        error_code=error_code,
        tags=[],
        meta={"sampled": sampled},
        observations=list(observations),
        scores=[],
    )


def test_burst_of_2000_traces_across_four_threads(tmp_path: Path) -> None:
    path = _make_db(tmp_path)
    flushed: list = []
    writer = Writer(path, queue_max=40, on_flush=flushed.append)
    writer.start()
    total = 2000
    barrier = threading.Barrier(4)

    def produce(start: int, stop: int) -> None:
        barrier.wait()
        for i in range(start, stop):
            writer.enqueue(
                _trace(
                    f"{i:08x}",
                    time.time(),
                    duration_ms=float(i % 97),
                    queue_ms=float(i % 13),
                    status=500 if i % 5 == 0 else 200,
                )
            )

    threads = [
        threading.Thread(target=produce, args=(i * 500, (i + 1) * 500)) for i in range(4)
    ]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        # the queue depth must stay bounded by queue_max while the burst runs
        assert writer.counters()["write_queue_depth"] <= 40
        time.sleep(0.001)
    for thread in threads:
        thread.join()
    writer.stop()

    counters = writer.counters()
    conn = db.connect(path)
    stored = count_traces(conn)
    stored_errors = conn.execute(
        "SELECT COUNT(*) FROM traces WHERE status = 500"
    ).fetchone()[0]
    stored_ids = sorted(row[0] for row in conn.execute("SELECT id FROM traces"))
    conn.close()

    assert counters["write_queue_depth"] == 0
    assert stored_errors == 400  # no error trace was ever dropped
    kept_total = total - 400
    kept_stored = stored - stored_errors
    # conservation: every kept trace is either persisted or counted, nothing vanishes
    assert kept_stored + counters["obs_dropped_total"] == kept_total
    assert stored == total - counters["obs_dropped_total"]
    assert counters["logs_dropped_total"] == 0
    assert counters["writes_total"] >= 1
    assert counters["write_latency_ms"] >= 0.0
    # on_flush batches contain exactly the persisted traces
    flushed_ids = sorted(trace.id for batch in flushed for trace in batch)
    assert flushed_ids == stored_ids


def test_failing_observation_rolls_back_the_whole_batch(tmp_path: Path) -> None:
    writer, flushed, path = _start(tmp_path)
    ts = time.time()
    first = _trace(
        "a1a1a1a1", ts, duration_ms=10.0, observations=(_obs("dup", "a1a1a1a1"),)
    )
    second = _trace(
        "b2b2b2b2",
        ts + 0.1,
        duration_ms=20.0,
        observations=(_obs("dup", "b2b2b2b2", start_ms=1.0),),  # duplicate observation id
    )
    writer.enqueue(first)
    writer.enqueue(second)
    writer.flush_now()
    counters = writer.counters()

    conn = db.connect(path)
    assert count_traces(conn) == 0  # nothing partial: the trace inserts rolled back too
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    conn.close()
    assert flushed == []  # no successful commit, so no on_flush callback
    assert counters["writes_total"] == 0
    assert counters["obs_dropped_total"] == 2  # the failed batch was dropped and counted


def test_rollup_rows_match_hand_computed_expectations(tmp_path: Path) -> None:
    path = _make_db(tmp_path)
    base = int(time.time() // 3600) * 3600 - 3600  # previous hour: buckets are expired
    writer = Writer(path)
    writer.start()
    traces = [
        _trace("00000001", base + 1, duration_ms=10, queue_ms=1,
               observations=(_obs("o0000001", "00000001"),)),
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

    def row(step: int, bucket: int, metric: str, model: str = "english",
            route: str = "/predict", status: int = 0) -> sqlite3.Row:
        found = load_rollup_row(conn, bucket, step, metric, model, route, status)
        assert found is not None, f"missing {metric} row at bucket={bucket} step={step}"
        return found

    # 10 s latency buckets: nearest-rank percentiles over the raw samples
    r = row(10, base, "latency")
    assert (r["count"], r["sum"], r["min"], r["max"]) == (3, 60.0, 10.0, 30.0)
    assert (r["p50"], r["p90"], r["p95"], r["p99"]) == (20.0, 30.0, 30.0, 30.0)
    r = row(10, base + 10, "latency")
    assert (r["count"], r["sum"], r["min"], r["max"]) == (2, 90.0, 40.0, 50.0)
    assert (r["p50"], r["p90"], r["p95"], r["p99"]) == (40.0, 50.0, 50.0, 50.0)

    # 60 s row: percentiles are the child-count-weighted merge of the two 10 s rows
    r = row(60, base, "latency")
    assert (r["count"], r["sum"], r["min"], r["max"]) == (5, 150.0, 10.0, 50.0)
    assert r["p50"] == pytest.approx((20 * 3 + 40 * 2) / 5)  # 28.0
    assert r["p90"] == pytest.approx((30 * 3 + 50 * 2) / 5)  # 38.0
    assert r["p95"] == pytest.approx(38.0)
    assert r["p99"] == pytest.approx(38.0)

    # 3600 s row receives the same increments via the cascade
    r = row(3600, base, "latency")
    assert (r["count"], r["sum"], r["min"], r["max"]) == (5, 150.0, 10.0, 50.0)
    assert r["p50"] == pytest.approx(28.0)

    # requests counter: one unit per trace, percentiles NULL
    r = row(10, base, "requests")
    assert (r["count"], r["sum"], r["min"], r["max"]) == (3, 3.0, 1.0, 1.0)
    assert r["p50"] is None and r["p99"] is None
    assert (row(60, base, "requests")["count"], row(60, base, "requests")["sum"]) == (5, 5.0)
    assert row(3600, base, "requests")["count"] == 5

    # errors aggregate by exact status with blanked model/route dimensions
    r = row(10, base, "errors", model="", route="", status=422)
    assert (r["count"], r["sum"], r["min"], r["max"]) == (1, 1.0, 1.0, 1.0)
    assert r["p50"] is None
    assert row(60, base, "errors", model="", route="", status=422)["count"] == 1
    assert load_rollup_row(conn, base, 10, "errors", "", "", 200) is None

    # queue_ms distribution across the same buckets
    r = row(10, base, "queue")
    assert (r["count"], r["sum"], r["min"], r["max"]) == (3, 9.0, 1.0, 5.0)
    assert (r["p50"], r["p90"], r["p95"], r["p99"]) == (3.0, 5.0, 5.0, 5.0)
    assert (row(10, base + 10, "queue")["count"], row(10, base + 10, "queue")["sum"]) == (2, 16.0)
    r = row(60, base, "queue")
    assert (r["count"], r["sum"], r["min"], r["max"]) == (5, 25.0, 1.0, 9.0)
    assert r["p50"] == pytest.approx((3 * 3 + 7 * 2) / 5)  # 4.6
    assert r["p90"] == pytest.approx((5 * 3 + 9 * 2) / 5)  # 6.6

    # all five traces are kept rows with their observation persisted
    assert count_traces(conn) == 5
    assert len(observations_for(conn, "00000001")) == 1
    conn.close()


def test_sampled_out_trace_updates_rollups_without_a_trace_row(tmp_path: Path) -> None:
    writer, flushed, path = _start(tmp_path)
    ts = time.time() - 60
    bucket = int(ts // 10) * 10
    kept = _trace("11111111", ts, duration_ms=100.0, queue_ms=2.0,
                  observations=(_obs("oo111111", "11111111"),))
    dropped = _trace("22222222", ts + 0.5, duration_ms=50.0, queue_ms=4.0, sampled=False)
    writer.enqueue(kept)
    writer.enqueue(dropped)
    writer.flush_now()
    writer.stop()

    conn = db.connect(path)
    assert count_traces(conn) == 1
    assert conn.execute("SELECT id FROM traces").fetchone()[0] == "11111111"
    assert conn.execute(
        "SELECT COUNT(*) FROM observations WHERE trace_id = '22222222'"
    ).fetchone()[0] == 0

    # counts and latency still include the sampled-out trace: charts stay accurate
    r = load_rollup_row(conn, bucket, 10, "requests", "english", "/predict", 0)
    assert (r["count"], r["sum"]) == (2, 2.0)
    r = load_rollup_row(conn, bucket, 10, "latency", "english", "/predict", 0)
    assert (r["count"], r["sum"], r["min"], r["max"]) == (2, 150.0, 50.0, 100.0)
    r = load_rollup_row(conn, bucket, 10, "queue", "english", "/predict", 0)
    assert (r["count"], r["sum"]) == (2, 6.0)
    conn.close()

    counters = writer.counters()
    assert counters["obs_dropped_total"] == 1  # sampling drop surfaced (architecture 9)
    assert [trace.id for batch in flushed for trace in batch] == ["11111111"]


def test_enqueue_log_persists_entry_with_trace_id(tmp_path: Path) -> None:
    writer, _flushed, path = _start(tmp_path)
    entry = {
        "ts": 1700000000.0,
        "level": "info",
        "source": "engine",
        "trace_id": "feedbee1",
        "message": "predict request_id=feedbee1 status=200 ms=412.5",
    }
    writer.enqueue_log(entry)
    writer.flush_now()
    counters = writer.counters()
    writer.stop()

    conn = db.connect(path)
    row = conn.execute(
        "SELECT ts, level, source, trace_id, message FROM log_entry"
    ).fetchone()
    conn.close()
    assert row is not None
    assert (
        row["ts"],
        row["level"],
        row["source"],
        row["trace_id"],
        row["message"],
    ) == (
        entry["ts"],
        entry["level"],
        entry["source"],
        entry["trace_id"],
        entry["message"],
    )
    assert counters["logs_dropped_total"] == 0
    assert counters["write_queue_depth"] == 0


def test_flush_now_is_deterministic(tmp_path: Path) -> None:
    path = _make_db(tmp_path)
    flushed: list = []
    writer = Writer(path, on_flush=flushed.append)
    for i in range(3):
        writer.enqueue(
            _trace(f"abc0000{i}", time.time(), duration_ms=5.0,
                   observations=(_obs(f"fo00000{i}", f"abc0000{i}"),))
        )
    writer.start()
    writer.flush_now()  # no sleeps: rows must be committed when this returns
    conn = db.connect(path)
    assert count_traces(conn) == 3
    counters = writer.counters()
    assert counters["writes_total"] == 1
    assert counters["write_queue_depth"] == 0
    assert counters["write_latency_ms"] >= 0.0
    assert [len(batch) for batch in flushed] == [3]

    writer.flush_now()  # nothing pending: returns without an extra transaction
    assert writer.counters()["writes_total"] == 1
    conn.close()
    writer.stop()


def test_stop_drains_the_queue_fully(tmp_path: Path) -> None:
    writer, flushed, path = _start(tmp_path)
    for i in range(500):
        writer.enqueue(_trace(f"{i:08x}", time.time(), duration_ms=1.0))
    writer.stop()  # no flush: stop itself must drain everything

    conn = db.connect(path)
    assert count_traces(conn) == 500
    conn.close()
    counters = writer.counters()
    assert counters["write_queue_depth"] == 0
    assert counters["obs_dropped_total"] == 0
    assert sum(len(batch) for batch in flushed) == 500


def test_full_queue_evicts_the_oldest_kept_non_error_trace(tmp_path: Path) -> None:
    path = _make_db(tmp_path)
    writer = Writer(path, queue_max=4)  # deliberately not started: no consumer
    for i in range(1, 6):
        writer.enqueue(
            _trace(f"aaaa000{i}", time.time(), duration_ms=1.0)
        )
    counters = writer.counters()
    assert counters["write_queue_depth"] == 4  # bounded at queue_max
    assert counters["obs_dropped_total"] == 1  # exactly the oldest was evicted

    writer.start()
    writer.flush_now()
    writer.stop()
    conn = db.connect(path)
    stored = sorted(row[0] for row in conn.execute("SELECT id FROM traces"))
    conn.close()
    assert stored == ["aaaa0002", "aaaa0003", "aaaa0004", "aaaa0005"]


def test_queue_full_of_errors_blocks_then_counts_drops(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(writer_module, "_PUT_TIMEOUT", 0.05)
    path = _make_db(tmp_path)
    writer = Writer(path, queue_max=3)  # not started: no consumer
    for i in range(3):
        writer.enqueue(_trace(f"e000000{i}", time.time(), duration_ms=1.0, status=500))
    assert writer.counters()["write_queue_depth"] == 3

    # an error trace never evicts another trace; its blocking put times out and is counted
    writer.enqueue(_trace("e9999999", time.time(), duration_ms=1.0, status=500))
    assert writer.counters()["write_queue_depth"] == 3
    assert writer.counters()["obs_dropped_total"] == 1

    # a kept trace finds no droppable candidate (queue is all errors) and times out
    writer.enqueue(_trace("k9999999", time.time(), duration_ms=1.0, status=200))
    assert writer.counters()["write_queue_depth"] == 3
    assert writer.counters()["obs_dropped_total"] == 2  # every drop is counted, none silent

    writer.start()
    writer.flush_now()
    writer.stop()
    conn = db.connect(path)
    stored = sorted(row[0] for row in conn.execute("SELECT id FROM traces"))
    conn.close()
    assert stored == ["e0000000", "e0000001", "e0000002"]  # queued errors were kept


def test_batch_rows_count_triggers_a_commit(tmp_path: Path) -> None:
    writer, _flushed, path = _start(tmp_path, batch_rows=3, batch_ms=50000)
    for i in range(3):
        writer.enqueue(_trace(f"c000000{i}", time.time(), duration_ms=2.0))
    conn = db.connect(path)
    deadline = time.time() + 2.0
    while time.time() < deadline and count_traces(conn) < 3:
        time.sleep(0.02)
    assert count_traces(conn) == 3  # only the batch_rows trigger can fire this fast
    conn.close()
    writer.stop()


def test_batch_ms_window_triggers_a_commit(tmp_path: Path) -> None:
    writer, _flushed, path = _start(tmp_path, batch_rows=200, batch_ms=100)
    writer.enqueue(_trace("d0000001", time.time(), duration_ms=2.0))
    conn = db.connect(path)
    deadline = time.time() + 2.0
    while time.time() < deadline and count_traces(conn) < 1:
        time.sleep(0.02)
    assert count_traces(conn) == 1  # only the batch_ms window can fire this
    conn.close()
    writer.stop()
