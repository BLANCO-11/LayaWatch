"""Single writer thread: bounded queue, one transaction per batch, rollup buckets.

Rules from docs/architecture.md section 3: handlers never write - they call ``enqueue``
(trace) or ``enqueue_log`` (log entry dict) and the writer owns every transaction. A batch
flushes at ``batch_rows`` items or ``batch_ms`` after its first item, in ONE transaction:
kept traces, then their observations and scores, then log rows, then rollup upserts; any
error rolls the whole batch back.

Backpressure (section 3 / section 6): when the queue is full an incoming kept non-error
trace (status < 400, no error_code, not sampled out) drops the OLDEST queued kept
non-error trace and counts it. Error and sampled-out incoming traces never evict - they
use a blocking put of ``_PUT_TIMEOUT`` seconds (a queue full of errors admits them as
batches drain) and, failing that, are dropped with a counter - a drop is never silent.
Logs never evict traces: a log arriving at a full queue is dropped and counted.
``obs_dropped_total`` counts sampling drops (a sampled-out trace still enqueues for
rollups), backpressure drops and failed-batch drops; ``logs_dropped_total`` counts log
losses the same way; ``writes_total`` counts committed transactions and
``write_latency_ms`` is the last commit's duration.

Rollups (docs/observability-model.md section 6): raw values accumulate per open 10 s
bucket keyed like the ``metric_rollup`` primary key (requests/errors are exact counters,
latency/queue are duration_ms/queue_ms distributions). A bucket closes when its end has
passed, or on ``flush_now``/``stop``. Closing summarizes only the NEW values, merges them
into any existing 10 s row and cascades that same increment - never the merged row -
into the 60 s and 3600 s parents via ``merge_rows``, so closing a bucket twice (flush,
then more traffic) never double-counts. Sampled-out traces (meta["sampled"] False, no
observations) insert no trace/observation/score rows but still update rollups, so charts
stay accurate under sampling.

SQLITE_BUSY: short backoff retries, then the batch is requeued once at the queue front;
if it still cannot commit it is dropped with counters and a warning (never silently).
``on_flush(traces)`` runs after every successful commit, outside the queue lock.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from layawatch.log import get_logger
from layawatch.obs.rollup import floor_bucket, summarize
from layawatch.store import db
from layawatch.store.queries import (
    COUNTER_METRICS,
    counter_stats,
    insert_observations,
    insert_scores,
    insert_trace,
    load_rollup_row,
    merge_stats,
    rollup_row,
    upsert_rollup,
)

if TYPE_CHECKING:  # pragma: no cover - the writer must never import the recorder at runtime
    from layawatch.obs.recorder import Trace

_LOG = get_logger("store")

_BUCKET_STEP = 10
_PARENT_STEPS = (60, 3600)
_PUT_TIMEOUT = 5.0
_BUSY_BACKOFF = (0.02, 0.05, 0.1)

#: One open 10 s bucket: counter events in ``n`` (requests/errors), distribution samples
#: in ``vals`` (latency duration_ms, queue queue_ms). Buckets are replaced, never
#: mutated, so in-memory state only advances when a commit succeeds (copy-on-write).
@dataclass
class _Bucket:
    n: int = 0
    vals: list[float] = field(default_factory=list)


def _is_kept(trace: Any) -> bool:
    return bool(trace.meta.get("sampled", True))


def _is_error(trace: Any) -> bool:
    return trace.status >= 400 or bool(trace.error_code)


def _is_busy(exc: sqlite3.Error) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - rollback of an already-dead transaction
            pass


class Writer:
    """Bounded queue drained by one thread in batched, atomic SQLite transactions."""

    def __init__(
        self,
        db_path: str,
        *,
        batch_rows: int = 200,
        batch_ms: int = 250,
        queue_max: int = 5000,
        on_flush: Callable[[list[Any]], None] | None = None,
    ) -> None:
        if batch_rows < 1:
            raise ValueError("batch_rows must be >= 1")
        if batch_ms < 1:
            raise ValueError("batch_ms must be >= 1")
        if queue_max < 1:
            raise ValueError("queue_max must be >= 1")
        self._db_path = str(db_path)
        self._batch_rows = batch_rows
        self._batch_ms = batch_ms / 1000.0
        self._queue_max = queue_max
        self._on_flush = on_flush
        self._queue: deque[tuple[str, Any]] = deque()
        self._cond = threading.Condition()
        self._flush_wanted = False
        self._flush_ack = threading.Event()
        self._stopping = False
        self._started = False
        self._thread: threading.Thread | None = None
        self._conn: sqlite3.Connection | None = None
        self._retried = False
        self._open: dict[tuple[int, str, str, str, int], _Bucket] = {}
        self._obs_dropped = 0
        self._logs_dropped = 0
        self._writes = 0
        self._write_latency_ms = 0.0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Open the writer connection on the writer thread and start draining."""
        with self._cond:
            if self._started:
                raise RuntimeError("writer already started")
            self._started = True
            self._thread = threading.Thread(
                target=self._run, name="layawatch-writer", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Drain the queue, close open rollup buckets, then join the thread."""
        thread = self._thread
        if thread is None:
            return
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        thread.join(timeout)
        if thread.is_alive():
            _LOG.warning("writer did not drain within stop timeout; thread still running")

    def flush_now(self, timeout: float = 5.0) -> None:
        """Block until everything enqueued so far is committed (or dropped and counted).

        Also closes every open rollup bucket, so rollup rows are queryable on return.
        """
        thread = self._thread
        if thread is None:
            raise RuntimeError("writer not started")
        if not thread.is_alive():
            return  # already stopped: stop() drained the queue
        with self._cond:
            self._flush_wanted = True
            self._flush_ack.clear()
            self._cond.notify_all()
        if not self._flush_ack.wait(timeout):
            raise TimeoutError("writer flush did not complete in time")

    # -- producer side -----------------------------------------------------

    def enqueue(self, trace: Trace) -> None:
        """Queue one finished trace; applies the pinned backpressure policy.

        On a full queue an incoming kept non-error trace evicts the oldest queued kept
        non-error trace (counted). Error and sampled-out traces never evict: they block
        up to ``_PUT_TIMEOUT`` for room and, failing that, are dropped with a counter.
        """
        with self._cond:
            if self._stopping:
                raise RuntimeError("writer is stopping; enqueue rejected")
            if not _is_kept(trace):
                # architecture section 9: obs_dropped_total covers sampling drops too;
                # the trace still queues so its rollups stay accurate.
                self._obs_dropped += 1
            if len(self._queue) >= self._queue_max:
                if _is_kept(trace) and not _is_error(trace):
                    if self._evict_kept_non_error():
                        self._obs_dropped += 1
                    elif not self._wait_for_room():
                        self._obs_dropped += 1
                        return
                elif not self._wait_for_room():
                    self._obs_dropped += 1
                    return
            self._queue.append(("trace", trace))
            self._cond.notify_all()

    def enqueue_log(self, entry: dict) -> None:
        """Queue one ``layawatch.log`` entry dict; a full queue drops the log, counted."""
        with self._cond:
            if self._stopping:
                raise RuntimeError("writer is stopping; enqueue rejected")
            if len(self._queue) >= self._queue_max:
                self._logs_dropped += 1
                return
            self._queue.append(("log", entry))
            self._cond.notify_all()

    def counters(self) -> dict:
        """Snapshot of the pulse counters surfaced by the UI (architecture section 9)."""
        with self._cond:
            return {
                "write_queue_depth": len(self._queue),
                "obs_dropped_total": self._obs_dropped,
                "logs_dropped_total": self._logs_dropped,
                "writes_total": self._writes,
                "write_latency_ms": self._write_latency_ms,
            }

    def _evict_kept_non_error(self) -> bool:
        """Remove the oldest droppable trace from the queue; False when none is queued."""
        for index, (kind, item) in enumerate(self._queue):
            if kind == "trace" and _is_kept(item) and not _is_error(item):
                del self._queue[index]
                return True
        return False

    def _wait_for_room(self) -> bool:
        """Block up to ``_PUT_TIMEOUT`` for queue space (error traces use this put)."""
        deadline = time.monotonic() + _PUT_TIMEOUT
        while len(self._queue) >= self._queue_max:
            if self._stopping:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._cond.wait(remaining)
        return True

    # -- writer thread -----------------------------------------------------

    def _run(self) -> None:
        try:
            conn = db.connect(self._db_path)
            # performance.md section 4.3 item 11/12: NORMAL under WAL is the documented
            # durability tradeoff (architecture section 5); the writer owns all writes.
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-16000")
            conn.execute("PRAGMA mmap_size=134217728")
            conn.isolation_level = None  # manual BEGIN/COMMIT per batch
            self._conn = conn
            while True:
                batch = self._collect()
                with self._cond:
                    stopping = self._stopping
                    flush = self._flush_wanted
                close_all = flush or stopping
                if not batch and not close_all and not self._due_buckets(time.time()):
                    continue  # idle wake with nothing due; _collect blocks again
                status = self._commit(batch, close_all=close_all)
                with self._cond:
                    if flush and status != "requeued" and not self._queue:
                        self._flush_wanted = False
                        self._flush_ack.set()
                    if stopping and status != "requeued" and not self._queue:
                        break
        except Exception as exc:  # pragma: no cover - defensive: surface a thread crash
            _LOG.error(f"writer thread crashed: {exc}")
        finally:
            conn = self._conn
            self._conn = None
            if conn is not None:
                _rollback(conn)
                conn.close()
            with self._cond:
                self._flush_wanted = False
                self._flush_ack.set()

    def _collect(self) -> list[tuple[str, Any]]:
        """Gather one batch: up to ``batch_rows`` items or ``batch_ms`` after the first.

        Flush and stop drain the queue regardless of the window; with nothing queued the
        wait ends when an open rollup bucket becomes due, so expired buckets close even
        when the writer is idle.
        """
        batch: list[tuple[str, Any]] = []
        t0 = 0.0
        with self._cond:
            while True:
                if self._queue:
                    draining = self._flush_wanted or self._stopping
                    if batch and not draining and (time.monotonic() - t0) >= self._batch_ms:
                        break  # window elapsed; queued items belong to the next batch
                    if not batch:
                        t0 = time.monotonic()
                    batch.append(self._queue.popleft())
                    self._cond.notify_all()
                    if len(batch) >= self._batch_rows:
                        break
                    continue
                if not batch:
                    if self._stopping or self._flush_wanted:
                        break  # drained; the loop decides whether work remains
                    if self._open:
                        wait = min(key[0] + _BUCKET_STEP for key in self._open) - time.time()
                        if wait <= 0:
                            break  # a bucket is already due; the loop closes it
                        self._cond.wait(wait)
                    else:
                        self._cond.wait(None)
                    continue
                if self._flush_wanted or self._stopping:
                    break
                remaining = self._batch_ms - (time.monotonic() - t0)
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
        return batch

    def _due_buckets(self, now: float) -> list[tuple[int, str, str, str, int]]:
        return [key for key in self._open if key[0] + _BUCKET_STEP <= now]

    def _commit(self, batch: list[tuple[str, Any]], *, close_all: bool) -> str:
        """Run one atomic batch; returns ``ok``, ``requeued`` (busy, try once more) or
        ``dropped`` (counted). In-memory rollup state advances only on success."""
        started = time.perf_counter()
        conn = self._conn
        assert conn is not None
        traces = [item for kind, item in batch if kind == "trace"]
        logs = [item for kind, item in batch if kind == "log"]
        kept = [trace for trace in traces if _is_kept(trace)]

        work = dict(self._open)
        for trace in traces:
            self._accumulate(work, trace)
        now = time.time()
        due = list(work) if close_all else [key for key in work if key[0] + _BUCKET_STEP <= now]
        if not batch and not due:
            return "ok"

        attempt = 0
        while True:
            attempt_work = dict(work)
            try:
                conn.execute("BEGIN IMMEDIATE")
                if kept:
                    insert_trace(conn, kept)
                    insert_observations(
                        conn, [obs for trace in kept for obs in trace.observations]
                    )
                    insert_scores(conn, [score for trace in kept for score in trace.scores])
                if logs:
                    conn.executemany(
                        "INSERT INTO log_entry (ts, level, source, trace_id, message)"
                        " VALUES (?,?,?,?,?)",
                        [
                            (
                                entry["ts"],
                                entry["level"],
                                entry["source"],
                                entry.get("trace_id"),
                                entry["message"],
                            )
                            for entry in logs
                        ],
                    )
                for key in due:
                    for row in self._rollup_rows(conn, attempt_work.pop(key), key):
                        upsert_rollup(conn, row)
                conn.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                _rollback(conn)
                if _is_busy(exc) and attempt < len(_BUSY_BACKOFF):
                    time.sleep(_BUSY_BACKOFF[attempt])
                    attempt += 1
                    continue
                if _is_busy(exc) and not self._retried:
                    with self._cond:
                        self._queue.extendleft(reversed(batch))
                    self._retried = True
                    return "requeued"
                self._drop_batch(batch, exc)
                return "dropped"
            except sqlite3.Error as exc:
                _rollback(conn)
                self._drop_batch(batch, exc)
                return "dropped"
            break

        self._open = attempt_work
        self._retried = False
        with self._cond:
            self._writes += 1
            self._write_latency_ms = (time.perf_counter() - started) * 1000.0
        if self._on_flush is not None:
            try:
                self._on_flush(list(kept))
            except Exception as exc:  # pragma: no cover - callbacks must not kill the writer
                _LOG.warning(f"on_flush callback failed: {exc}")
        return "ok"

    def _drop_batch(self, batch: list[tuple[str, Any]], exc: Exception) -> None:
        """Count a lost batch - drops are surfaced, never silent."""
        with self._cond:
            for kind, _item in batch:
                if kind == "trace":
                    self._obs_dropped += 1
                else:
                    self._logs_dropped += 1
            self._retried = False
        _LOG.warning(f"batch write failed ({exc}); {len(batch)} entries dropped and counted")

    # -- rollup pipeline ---------------------------------------------------

    def _accumulate(
        self, work: dict[tuple[int, str, str, str, int], _Bucket], trace: Any
    ) -> None:
        """Add one trace's rollup contributions (copy-on-write; safe to retry)."""
        bucket = floor_bucket(trace.ts_start, _BUCKET_STEP)
        model = trace.model or ""
        route = trace.route
        status = trace.status
        self._bump(work, (bucket, "requests", model, route, 0), n=1)
        if status >= 400:
            self._bump(work, (bucket, "errors", "", "", status), n=1)
        self._bump(work, (bucket, "latency", model, route, 0), value=float(trace.duration_ms))
        self._bump(work, (bucket, "queue", model, route, 0), value=float(trace.queue_ms))

    @staticmethod
    def _bump(
        work: dict[tuple[int, str, str, str, int], _Bucket],
        key: tuple[int, str, str, str, int],
        *,
        n: int = 0,
        value: float | None = None,
    ) -> None:
        current = work.get(key)
        if current is None:
            work[key] = _Bucket(n=n, vals=[value] if value is not None else [])
        elif value is None:
            work[key] = _Bucket(n=current.n + n, vals=current.vals)
        else:
            work[key] = _Bucket(n=current.n, vals=[*current.vals, value])

    def _rollup_rows(
        self,
        conn: sqlite3.Connection,
        bucket: _Bucket,
        key: tuple[int, str, str, str, int],
    ) -> list[dict]:
        """Rows for one closed bucket: summarize the NEW values, merge into the 10 s row,
        cascade the same increment into the 60 s and 3600 s parents (merge_stats reads
        the existing row first). Reads run inside the open transaction."""
        b, metric, model, route, status = key
        if metric in COUNTER_METRICS:
            if bucket.n <= 0:
                return []
            stats = counter_stats(bucket.n)
        else:
            if not bucket.vals:
                return []
            stats = summarize(bucket.vals)
        existing = load_rollup_row(conn, b, _BUCKET_STEP, metric, model, route, status)
        rows = [
            rollup_row(b, _BUCKET_STEP, metric, model, route, status,
                       merge_stats(metric, existing, stats))
        ]
        for step in _PARENT_STEPS:
            parent = floor_bucket(b, step)
            parent_row = load_rollup_row(conn, parent, step, metric, model, route, status)
            rows.append(
                rollup_row(parent, step, metric, model, route, status,
                           merge_stats(metric, parent_row, stats))
            )
        return rows
