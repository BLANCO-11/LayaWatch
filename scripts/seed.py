"""Seed a throwaway LayaWatch state directory with performance-scale, realistic data.

Builds exactly what the phase 7 budgets measure against (plan task 1; the plan's risk
"seeded database shape differs from production" is answered by mirroring the real
route/status/model mix and retention state)::

    LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/seed.py --traces 10000 --observations 9

What lands in the database:

- traces via ``tests/seed.py`` (the shared helpers: status cycle 90/4/2/3/1 over 200/422/400/
  500/503, route mix 80 % /predict + 20 % /route, model cycle english/multilingual/null,
  latency-shaped duration/queue/forward columns), seeded ``--traces`` plus a 10 % head that the
  retention pass then prunes back to the cap, so the cap is actually exercised;
- ``--observations`` lifecycle spans per trace from the frozen vocabulary
  (``layawatch/obs/vocabulary.py``) in waterfall order, durations sharing 95 % of the trace
  (the rest is the unspanned ``meta.framework_ms`` of a real trace), errors landing on the span
  where the failure class belongs, ``input``/``output`` NULL as with capture disabled;
- metric rollups covering the whole trace window (the writer's view), plus sparse
  ``errors``-metric rows at the seeded status mix so ``metrics?metrics=...errors...`` is not
  a flat zero;
- a log tail of ``log_ring_size + 500`` rows and an audit history, so the caps prune them too;
- ONE retention pass with the server's own config caps (``layawatch/store/retention.py
  apply``): rollups already complete, traces pruned to ``retention_traces``, logs to
  ``log_ring_size``, WAL checkpointed - the retention state a live box sits in.

Idempotent and marker-guarded: a state directory whose traces were not written by this script
carries no ``.lw-seed.json`` marker and is refused untouched (the live box's database is never
seeded); a marked directory is wiped and reproduced with identical counts. Row counts, the
retention pass result and the database size are printed for evidence. Run it BEFORE starting
any server: ``scripts/bench.py`` and ``scripts/budget_check.py`` read the same directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from budget_check import state_dir as shared_state_dir  # noqa: E402

from layawatch.config import Config  # noqa: E402
from layawatch.obs.recorder import Observation  # noqa: E402
from layawatch.obs.vocabulary import SPAN_NAMES, observation_type  # noqa: E402
from layawatch.store import queries  # noqa: E402
from layawatch.store.db import connect, migrate  # noqa: E402
from layawatch.store.retention import apply as retention_apply  # noqa: E402
from tests.seed import seed_logs, seed_rollups, seed_traces  # noqa: E402

MARKER_NAME = ".lw-seed.json"

#: Lifecycle spans of one /predict trace in waterfall order (frozen vocabulary, obs-model 2).
#: ``model.load`` (real-engine cold start) and ``error`` (failures only) are not part of a
#: normal trace and are deliberately absent.
LIFECYCLE = (
    "http.receive",
    "auth.verify",
    "body.parse",
    "lang.detect",
    "route.decide",
    "queue.wait",
    "forward",
    "serialize",
    "response.send",
)
assert set(LIFECYCLE) <= SPAN_NAMES, "lifecycle name left the frozen vocabulary"

#: Share of the trace duration each span receives (sums to 0.95: the rest is the unspanned
#: framework time real traces report as meta.framework_ms).
_WEIGHTS = (0.02, 0.01, 0.02, 0.03, 0.03, 0.04, 0.60, 0.15, 0.05)

#: Audit actions cycled over the window (docs/api-reference.md section 10 vocabulary).
_AUDIT_ACTIONS = (
    ("auth.login", "ok"),
    ("auth.login", "ok"),
    ("settings.updated", "ok"),
    ("key.created", "ok"),
    ("ratelimit.updated", "ok"),
    ("trace.deleted", "ok"),
    ("auth.login_failed", "denied"),
    ("key.revoked", "ok"),
)
_AUDIT_ACTORS = ("owner@acme.com", "admin@acme.com", "viewer@acme.com", "system")


def _error_span(status: int) -> str:
    """The lifecycle span that carries ``status='error''`` for a failing trace."""
    if status >= 500:
        return "forward"
    if status == 422:
        return "lang.detect"
    if status == 400:
        return "body.parse"
    return "route.decide"


def _obs_id(trace_id: str, index: int) -> str:
    """Deterministic 32-hex observation id (uuid4().hex shape, reproducible across runs)."""
    return hashlib.sha256(f"{trace_id}:{index}".encode()).hexdigest()[:32]


def seed_observations(conn, count: int) -> int:
    """Attach ``count`` lifecycle spans to every seeded trace; returns rows inserted.

    Chunked per 500 traces so a 10k-trace seed never builds a 90k-object list, committed per
    chunk (``queries.insert_observations`` runs inside the caller's transaction).
    """
    if count <= 0:
        return 0
    names = LIFECYCLE[: min(count, len(LIFECYCLE))]
    factor = 0.95 / sum(_WEIGHTS[: len(names)])
    traces = conn.execute(
        "SELECT id, duration_ms, status, model FROM traces ORDER BY id"
    ).fetchall()
    inserted = 0
    for offset in range(0, len(traces), 500):
        batch: list[Observation] = []
        for trace in traces[offset : offset + 500]:
            failing = _error_span(int(trace["status"])) if int(trace["status"]) >= 400 else None
            start = 0.0
            for index, name in enumerate(names):
                duration = _WEIGHTS[index] * factor * float(trace["duration_ms"])
                batch.append(
                    Observation(
                        id=_obs_id(trace["id"], index),
                        trace_id=trace["id"],
                        parent_id=None,
                        name=name,
                        type=observation_type(name),
                        start_ms=round(start, 3),
                        duration_ms=round(duration, 3),
                        status="error" if name == failing else "ok",
                        model=trace["model"],
                        input=None,
                        output=None,
                        meta=None,
                    )
                )
                start += duration
        queries.insert_observations(conn, batch)
        conn.commit()
        inserted += len(batch)
    return inserted


def seed_error_rollups(conn, seed: int) -> int:
    """Add sparse ``errors``-metric rows at the seeded status mix and cascade the parents.

    ``tests/seed.py`` covers requests/latency/queue; without this the ``errors`` series a
    budgeted metrics query asks for would be a flat zero (not the real error cadence).
    """
    rng = random.Random(seed)
    buckets = [
        row[0]
        for row in conn.execute("SELECT DISTINCT bucket FROM metric_rollup WHERE step = 10")
    ]
    codes = (400, 422, 500, 503)
    rows = []
    for bucket in buckets:
        if rng.random() < 0.6:
            continue  # quiet bucket: real error cadence is sparse
        by_code: dict[int, int] = {}
        for _ in range(1 if rng.random() < 0.8 else 2):
            # _STATUS_CYCLE proportions; repeated codes merge into one row (PK is per bucket)
            code = rng.choices(codes, weights=(2, 4, 3, 1))[0]
            by_code[code] = by_code.get(code, 0) + rng.randint(1, 3)
        for code, count in sorted(by_code.items()):
            rows.append(
                (bucket, 10, "errors", "", "", code, count, float(count), 1.0, 1.0,
                 None, None, None, None)
            )
    if rows:
        conn.executemany(
            "INSERT INTO metric_rollup (bucket, step, metric, model, route, status,"
            " count, sum, min, max, p50, p90, p95, p99) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    for parent in (60, 3600):
        conn.execute(
            f"INSERT INTO metric_rollup (bucket, step, metric, model, route, status,"
            f" count, sum, min, max, p50, p90, p95, p99)"
            f" SELECT (bucket/{parent})*{parent}, {parent}, metric, model, route, status,"
            f" SUM(count), SUM(sum), MIN(min), MAX(max), NULL, NULL, NULL, NULL"
            f" FROM metric_rollup WHERE step = 10 AND metric = 'errors'"
            f" GROUP BY bucket/{parent}, metric, model, route, status"
        )
    conn.commit()
    return len(rows)


def seed_audit(conn, count: int, span_days: float, now: float) -> int:
    """Append ``count`` audit entries spread over ``span_days``.

    Raw SQL because ``queries.insert_audit`` stamps ``time.time()``: a spread over the seed
    window needs an explicit ``ts`` column.
    """
    start = now - span_days * 86400.0
    rows = []
    for i in range(count):
        ts = start + (now - start) * (i / max(count - 1, 1))
        action, result = _AUDIT_ACTIONS[i % len(_AUDIT_ACTIONS)]
        actor = _AUDIT_ACTORS[i % len(_AUDIT_ACTORS)]
        rows.append((ts, None, actor, action, None, result, None))
    conn.executemany(
        "INSERT INTO audit_log (ts, actor_id, actor, action, target, result, meta)"
        " VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def row_counts(conn) -> dict[str, int]:
    """Rows per seeded table, smallest first (evidence line)."""
    tables = (
        "traces",
        "observations",
        "scores",
        "log_entry",
        "metric_rollup",
        "audit_log",
        "users",
        "sessions",
        "api_keys",
    )
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def file_bytes(db_path: Path) -> dict[str, int]:
    """Bytes of ``state.sqlite3`` plus its ``-wal``/``-shm`` sidecars (missing files read 0)."""
    sizes = {}
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        sizes[path.name] = path.stat().st_size if path.exists() else 0
    return sizes


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed.py",
        description=(
            "Seed LAYA_STATE_DIR (default '.') with a realistic performance-scale database:"
            " traces, lifecycle spans, rollups, logs, audit rows, then one retention pass."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/seed.py"
            " --traces 10000 --observations 9\n"
            "Idempotent: a marked directory is wiped and reproduced with identical counts;\n"
            "a directory with foreign (unmarked) traces is refused untouched.\n"
            "Run before scripts/bench.py and scripts/budget_check.py - they read the same db."
        ),
    )
    parser.add_argument("--traces", type=int, default=10000, help="trace rows after retention")
    parser.add_argument(
        "--observations", type=int, default=9, help="lifecycle spans per trace (0 disables)"
    )
    parser.add_argument(
        "--span-days", type=float, default=5.0, help="trace spread in days (keep < retention_days)"
    )
    parser.add_argument("--seed", type=int, default=1234, help="RNG seed for the shared helpers")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.traces < 0 or args.observations < 0 or args.span_days <= 0:
        print("seed: --traces/--observations must be >= 0 and --span-days > 0", file=sys.stderr)
        return 2

    target = shared_state_dir()
    cfg = Config.load()
    db_path = target / cfg.db_path.name
    marker_path = target / MARKER_NAME
    target.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        migrate(conn)
        existing = int(conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0])
        if existing and not marker_path.exists():
            print(
                f"seed: REFUSING {db_path}: {existing} traces and no {MARKER_NAME} marker;"
                " this database was not seeded by this script (foreign or live data).",
                file=sys.stderr,
            )
            return 1
        # Wipe every seeded table (traces cascade observations and scores); restarting the
        # AUTOINCREMENT sequences keeps regenerated ids identical across runs.
        for statement in (
            "DELETE FROM traces",
            "DELETE FROM log_entry",
            "DELETE FROM metric_rollup",
            "DELETE FROM audit_log",
            "DELETE FROM sqlite_sequence WHERE name IN ('log_entry', 'audit_log')",
        ):
            conn.execute(statement)
        conn.commit()

        # Intent marker: a crashed run leaves it behind so the next run may wipe and retry
        # (a live or foreign database is refused above and never reaches this line).
        marker_path.write_text(
            json.dumps(
                {
                    "status": "in_progress",
                    "params": {
                        "traces": args.traces,
                        "observations": args.observations,
                        "span_days": args.span_days,
                        "seed": args.seed,
                    },
                },
                indent=2,
            )
            + "\n"
        )

        seed_n = args.traces
        if args.traces >= cfg.retention_traces:
            seed_n = args.traces + args.traces // 10  # 10 % head the retention pass prunes
            if args.traces > cfg.retention_traces:
                print(
                    f"seed: --traces {args.traces} exceeds retention_traces"
                    f" {cfg.retention_traces}: retention leaves {cfg.retention_traces}"
                )
        else:
            print(
                f"seed: --traces {args.traces} is below retention_traces"
                f" {cfg.retention_traces}: seeding exactly {args.traces}, nothing to prune"
            )
        print(f"seed: state {target} (db {db_path.name})")
        print(
            f"seed: inserting {seed_n} traces over {args.span_days}d"
            f" + rollups + logs + audit, then one retention pass"
        )

        started = time.perf_counter()
        traces_rows = seed_traces(conn, n=seed_n, span_days=args.span_days, seed=args.seed)
        obs_rows = seed_observations(conn, args.observations)
        rollup_rows = seed_rollups(conn, span_days=args.span_days)
        error_rows = seed_error_rollups(conn, args.seed)
        log_target = cfg.log_ring_size + 500
        log_rows = seed_logs(conn, n=log_target)
        audit_rows = seed_audit(conn, 400, args.span_days, now=time.time())

        # One retention pass with the server's own caps; the writer is not running, so the
        # queue-idle gate is omitted exactly as retention.apply documents.
        retention = retention_apply(
            conn,
            max_traces=cfg.retention_traces,
            max_logs=cfg.log_ring_size,
            max_age_days=float(cfg.retention_days),
        )
        counts = row_counts(conn)
        sizes = file_bytes(db_path)

        marker_path.write_text(
            json.dumps(
                {
                    "status": "seeded",
                    "seeded_at": time.strftime("%Y%m%d-%H%M%S"),
                    "params": {
                        "traces": args.traces,
                        "observations": args.observations,
                        "span_days": args.span_days,
                        "seed": args.seed,
                    },
                    "inserted": {
                        "traces": traces_rows,
                        "observations": obs_rows,
                        "metric_rollup_10s": rollup_rows,
                        "error_rollup_rows": error_rows,
                        "log_entry": log_rows,
                        "audit_log": audit_rows,
                    },
                    "retention": retention,
                    "rows_after_retention": counts,
                    "bytes": sizes,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        conn.close()

    print(f"seed: inserted traces={traces_rows} observations={obs_rows}"
          f" rollups(+errors)={rollup_rows}+{error_rows} logs={log_rows} audit={audit_rows}")
    print(f"seed: retention pass {json.dumps(retention, sort_keys=True)}")
    print("seed: rows after retention " + json.dumps(counts, sort_keys=True))
    total = sum(sizes.values())
    print(
        "seed: db size "
        + " ".join(f"{name}={size}" for name, size in sizes.items())
        + f" total={total} bytes ({total / (1024 * 1024):.2f} MB)"
    )
    print(f"seed: marker {marker_path} ({time.perf_counter() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
