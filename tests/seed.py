"""Synthetic seed data for contract and performance tests.

Not a test module: helpers insert traces, rollups and log rows directly (fast enough for
10k-row performance budgets without running the engine or the writer).
"""
from __future__ import annotations

import json
import random
import sqlite3
import time

_STATUS_CYCLE = [200] * 90 + [422] * 4 + [400] * 2 + [500] * 3 + [503] * 1
_ROUTES = ["/predict"] * 8 + ["/route"] * 2
_MODELS = ["english", "multilingual", None]


def seed_traces(
    conn: sqlite3.Connection,
    n: int = 10000,
    *,
    span_days: float = 3.0,
    now: float | None = None,
    seed: int = 1234,
) -> int:
    """Insert ``n`` synthetic traces newest-first over ``span_days``; returns rows inserted."""
    rng = random.Random(seed)
    now = time.time() if now is None else now
    start = now - span_days * 86400.0
    rows = []
    for i in range(n):
        ts = start + (now - start) * (i / max(n - 1, 1)) + rng.uniform(-0.5, 0.5)
        status = _STATUS_CYCLE[i % len(_STATUS_CYCLE)]
        route = _ROUTES[i % len(_ROUTES)]
        model = _MODELS[i % len(_MODELS)]
        rows.append(
            (
                f"{i:08x}",
                ts,
                round(abs(rng.gauss(400, 250)) + 5.0, 3),
                route,
                "POST" if route == "/predict" else "POST",
                status,
                model,
                "English Latin text" if model else None,
                "en" if model else "mul",
                None if status < 400 else "internal_error",
                round(abs(rng.gauss(2, 8)), 3),
                round(abs(rng.gauss(380, 220)) + 5.0, 3) if route == "/predict" else None,
                rng.randint(200, 2048),
                rng.randint(1, 5),
                f"k_{rng.randrange(16**6):06x}" if i % 3 == 0 else None,
                None,
                None if status < 400 else "synthetic failure",
                json.dumps(["seed"]) if i % 10 == 0 else "[]",
                json.dumps({"seeded": True}),
            )
        )
    conn.executemany(
        "INSERT INTO traces (id, ts_start, duration_ms, route, method, status, model,"
        " route_reason, lang, error_code, queue_ms, forward_ms, state_bytes, question_count,"
        " client_key_id, session_id, error_message, tags, meta)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def seed_rollups(
    conn: sqlite3.Connection,
    *,
    span_days: float = 3.0,
    now: float | None = None,
    step: int = 10,
) -> int:
    """Fill metric_rollup 10 s/60 s/3600 s rows covering the same window as seed_traces."""
    now = time.time() if now is None else now
    start = now - span_days * 86400.0
    rows10 = []
    b = int(start // step) * step
    while b < now:
        rows10.append((b, step, "requests", "english", "/predict", 0, 4, 4.0, 1.0, 4.0,
                       None, None, None, None))
        rows10.append((b, step, "latency", "english", "/predict", 0, 4, 1600.0, 120.0, 900.0,
                       350.0, 700.0, 850.0, 950.0))
        rows10.append((b, step, "queue", "english", "/predict", 0, 4, 12.0, 0.0, 9.0,
                       0.5, 3.0, 6.0, 8.0))
        b += step
    conn.executemany(
        "INSERT OR REPLACE INTO metric_rollup (bucket, step, metric, model, route, status,"
        " count, sum, min, max, p50, p90, p95, p99) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows10,
    )
    # cascade into 60 s and 3600 s with exact counters
    for parent in (60, 3600):
        # bucket-aligned parents
        rows = conn.execute(
            "SELECT bucket, metric, model, route, status, COUNT(*) n, SUM(count) c, SUM(sum) s,"
            " MIN(min) mn, MAX(max) mx FROM metric_rollup WHERE step = ?"
            " GROUP BY bucket, metric, model, route, status",
            (step,),
        ).fetchall()
        by_parent: dict[tuple, list] = {}
        for bucket, metric, model, route, status, _n, c, s, mn, mx in rows:
            pb = (bucket // parent) * parent
            by_parent.setdefault((pb, metric, model, route, status), []).append((c, s, mn, mx))
        for (pb, metric, model, route, status), parts in by_parent.items():
            conn.execute(
                "INSERT OR REPLACE INTO metric_rollup (bucket, step, metric, model, route,"
                " status, count, sum, min, max, p50, p90, p95, p99)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pb, parent, metric, model, route, status,
                 sum(p[0] for p in parts), sum(p[1] for p in parts),
                 min(p[2] for p in parts), max(p[3] for p in parts),
                 None, None, None, None),
            )
    conn.commit()
    return len(rows10)


def seed_logs(conn: sqlite3.Connection, n: int = 500, *, now: float | None = None) -> int:
    """Insert ``n`` synthetic log entries (levels cycle) newest-last."""
    now = time.time() if now is None else now
    levels = ["debug", "info", "warning", "error"]
    rows = [
        (now - (n - i), levels[i % 4], "server", f"{i:08x}" if i % 5 == 0 else None,
         f"seeded line {i}")
        for i in range(n)
    ]
    conn.executemany(
        "INSERT INTO log_entry (ts, level, source, trace_id, message) VALUES (?,?,?,?,?)", rows
    )
    conn.commit()
    return len(rows)
