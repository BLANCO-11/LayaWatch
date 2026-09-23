"""Measurement harness for the LayaWatch performance budgets: ``overhead``, ``throughput``,
``api``, ``static``, ``sse``, ``all``.

Budgets and the harness contract come from ``docs/performance.md`` sections 1 and 2::

    overhead    recording overhead added to engine latency   <= 3 ms p95
    api         trace list query on the 10k seeded database  <= 50 ms p95
    api         metrics query, 15 m window                   <= 30 ms p95
    static      static asset response, 304 when unchanged    <= 5 ms p95
    sse         tick cost with 20 connected clients          <=  5 ms per tick

Usage::

    LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py all
    LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py api
    .venv/bin/python scripts/bench.py static --strict

Every run spawns its OWN throwaway server on ``LW_BENCH_PORT`` (default 8091) against the seeded
``LAYA_STATE_DIR`` (default ``.``) and SIGTERMs it in a ``finally`` block; the child boots
engineless (the launch command nulls ``sys.modules["laya"]`` before the ``find_spec`` probe in
``layawatch/__main__.py``, so the deterministic FakeAdapter serves and no checkpoint loads -
P1-I3 warns against parallel engine loads). Server-spawn, wait, stop and JSON-result helpers are
shared with ``scripts/budget_check.py`` (same directory), and the engine payload is the canonical
``scripts/smoke_laya.py`` request, so seed, bench and budget all read one database and one
workload. Every subcommand prints a table of p50/p90/p95/p99/max/n in milliseconds and writes
the run to ``bench/results/<timestamp>.json`` (``all`` writes one combined file).

Exit codes: 0 when every series was measured (budget verdicts are recorded in the JSON either
way), 1 on a harness error (port busy, server died, unexpected status, unseeded database), or -
with ``--strict`` - 1 when any budget verdict fails. CI uses ``--strict`` at 2x budget
(``docs/performance.md`` section 2); the hard resource gate is ``scripts/budget_check.py``.
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import selectors
import socket
import sqlite3
import sys
import threading
import time
import zlib
from pathlib import Path

import budget_check
from budget_check import HarnessError, spawn_server, state_dir, stop_server, wait_serving

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smoke_laya import QUESTIONS, STATE  # noqa: E402  (canonical routed request, scripts/ dir)

from layawatch.config import Config  # noqa: E402  (repo-root import after the path bootstrap)

BENCH_PORT = int(os.environ.get("LW_BENCH_PORT", "8091"))
REQUEST_TIMEOUT_S = 30.0
MIN_SEED_TRACES = 1000  # ``api`` refuses an unseeded database instead of reporting fiction
DEEP_CURSOR_OFFSET = 5000  # acceptance: the trace budget must cover a cursor deep in the table
DEEP_PAGE_LIMIT = 50  # same page size as the shallow series, so the two are comparable
DEEP_MAX_PAGES = 200
SSE_CLIENTS = 20
SSE_WINDOW_S = 45.0
SSE_SKIP_WAVES = 2  # first waves carry thread-pool spin-up and connection skew
PREDICT_BODY = json.dumps({"state": STATE, "questions": QUESTIONS})

_BUDGET_LIMITS_MS = {
    "overhead": 3.0,
    "traces": 50.0,
    "metrics_15m": 30.0,
    "static": 5.0,
    "sse_tick": 5.0,
}


# --------------------------------------------------------------------------- statistics
def stats(values: list[float]) -> dict:
    """``{p50, p90, p95, p99, max, n}`` of millisecond samples (nearest-rank percentiles)."""
    if not values:
        raise HarnessError("measurement produced no samples")
    ordered = sorted(values)
    count = len(ordered)

    def nearest(percent: float) -> float:
        return ordered[max(0, math.ceil(percent * count / 100) - 1)]

    return {
        "p50": round(nearest(50), 3),
        "p90": round(nearest(90), 3),
        "p95": round(nearest(95), 3),
        "p99": round(nearest(99), 3),
        "max": round(ordered[-1], 3),
        "n": count,
    }


def print_table(title: str, series: dict[str, dict]) -> None:
    print(f"\n{title} (milliseconds)")
    print(f"{'series':<24}{'p50':>10}{'p90':>10}{'p95':>10}{'p99':>10}{'max':>10}{'n':>8}")
    for name, row in series.items():
        print(
            f"{name:<24}{row['p50']:>10.3f}{row['p90']:>10.3f}{row['p95']:>10.3f}"
            f"{row['p99']:>10.3f}{row['max']:>10.3f}{row['n']:>8}"
        )


def print_verdicts(budgets: dict[str, dict]) -> None:
    for name, budget in budgets.items():
        verdict = "PASS" if budget["pass"] else "FAIL"
        line = (
            f"budget {name}: {budget['value']} {budget['unit']}"
            f" (limit {budget['limit']} {budget['unit']}) -> {verdict}"
        )
        if budget.get("note"):
            line += f"  [{budget['note']}]"
        print(line)


# ------------------------------------------------------------------------- http plumbing
class Client:
    """Keep-alive HTTP client that times one full request/response round trip in ms."""

    def __init__(self, port: int) -> None:
        self._port = port
        self._conn = http.client.HTTPConnection("127.0.0.1", port, timeout=REQUEST_TIMEOUT_S)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, float, bytes, dict[str, str]]:
        payload = dict(headers or {})
        payload.setdefault("Accept-Encoding", "gzip")
        if body is not None:
            payload.setdefault("Content-Type", "application/json")
        last_error: Exception | None = None
        for attempt in (1, 2):  # one transparent reconnect: the server may have timed us out
            start = time.perf_counter()
            try:
                self._conn.request(method, path, body=body, headers=payload)
                response = self._conn.getresponse()
                raw = response.read()
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                if response.getheader("Content-Encoding") == "gzip":
                    raw = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
                response_headers = {key.lower(): value for key, value in response.getheaders()}
                return response.status, elapsed_ms, raw, response_headers
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                self._conn.close()
                self._conn = http.client.HTTPConnection(
                    "127.0.0.1", self._port, timeout=REQUEST_TIMEOUT_S
                )
        raise HarnessError(f"{method} {path} failed twice: {last_error}")

    def close(self) -> None:
        self._conn.close()


def start_server(log_name: str, extra_env: dict[str, str] | None = None) -> object:
    """Spawn the engineless bench server on ``BENCH_PORT``; caller must ``stop_server`` it."""
    target = state_dir()
    proc, launched = spawn_server(
        state_dir=target,
        port=BENCH_PORT,
        log_name=log_name,
        extra_env=extra_env,
    )
    wait_serving(proc, launched, target / log_name, port=BENCH_PORT)
    return proc


def predict(client: Client) -> float:
    """One canonical ``POST /predict`` round trip; returns elapsed ms."""
    status, elapsed_ms, raw, _ = client.request("POST", "/predict", body=PREDICT_BODY)
    if status != 200:
        raise HarnessError(f"POST /predict -> {status}: {raw[:200]!r}")
    return elapsed_ms


def get_json(client: Client, path: str) -> tuple[dict, float]:
    """One ``GET`` returning a JSON object; asserts 200 and returns ``(payload, elapsed_ms)``."""
    status, elapsed_ms, raw, _ = client.request("GET", path)
    if status != 200:
        raise HarnessError(f"GET {path} -> {status}: {raw[:200]!r}")
    return json.loads(raw), elapsed_ms


def timed_gets(client: Client, path: str, count: int) -> list[float]:
    """Warm the path once, then time ``count`` GETs (warm-up samples are discarded)."""
    get_json(client, path)
    samples: list[float] = []
    for _ in range(count):
        _, elapsed_ms = get_json(client, path)
        samples.append(elapsed_ms)
    return samples


def db_trace_count() -> int:
    """Current ``traces`` row count of the shared seeded database (read-only)."""
    db_path = state_dir() / Config().db_path.name
    if not db_path.exists():
        raise HarnessError(f"no database at {db_path}; run scripts/seed.py first")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0])
    finally:
        conn.close()


# -------------------------------------------------------------------------- subcommands
def run_overhead() -> dict:
    """Recording overhead: p95 of (record_on - record_off) over >= 200 ``/predict`` requests."""
    count = 250
    series: dict[str, dict] = {}
    for label, record in (("record_on", "1"), ("record_off", "0")):
        proc = start_server(f"bench-overhead-{label}.log", {"LAYWATCH_RECORD": record})
        client = Client(BENCH_PORT)
        try:
            for _ in range(10):
                predict(client)  # settle the thread pool and the keep-alive connection
            series[label] = stats([predict(client) for _ in range(count)])
        finally:
            client.close()
            stop_server(proc)
    on, off = series["record_on"], series["record_off"]
    added = {
        key: round(on[key] - off[key], 3) for key in ("p50", "p90", "p95", "p99", "max")
    }
    added["n"] = count
    series["overhead_added"] = added
    value = added["p95"]
    return {
        "series": series,
        "budgets": {
            "overhead": {
                "limit": _BUDGET_LIMITS_MS["overhead"],
                "unit": "ms",
                "value": value,
                "pass": value <= _BUDGET_LIMITS_MS["overhead"],
                "note": (
                    f"p95 of (record_on - record_off), n={count} per side,"
                    " POST /predict, fake engine, LAYWATCH_RECORD=1 vs 0"
                ),
            }
        },
        "scalars": {},
    }


def run_throughput() -> dict:
    """Engine throughput: sequential ``/predict`` latency table plus requests per second."""
    count = 300
    proc = start_server("bench-throughput.log")
    client = Client(BENCH_PORT)
    try:
        for _ in range(20):
            predict(client)
        start = time.perf_counter()
        samples = [predict(client) for _ in range(count)]
        wall_s = time.perf_counter() - start
    finally:
        client.close()
        stop_server(proc)
    rps = round(count / wall_s, 1) if wall_s > 0 else 0.0
    print(f"throughput: {rps} req/s ({count} requests in {wall_s:.2f} s, fake engine)")
    return {
        "series": {"predict_latency": stats(samples)},
        "budgets": {},
        "scalars": {"throughput_rps": rps, "wall_s": round(wall_s, 3), "requests": count},
    }


def run_api() -> dict:
    """List-query budgets: trace list (shallow + deep cursor) and the 15 m metrics query."""
    traces_at_start = db_trace_count()
    if traces_at_start < MIN_SEED_TRACES:
        raise HarnessError(
            f"only {traces_at_start} traces in {state_dir()};"
            " seed first: scripts/seed.py --traces 10000 --observations 9"
        )
    proc = start_server("bench-api.log")
    client = Client(BENCH_PORT)
    try:
        shallow = timed_gets(client, "/api/v1/traces?limit=50", 100)

        # Deep cursor: follow keyset pages until at least DEEP_CURSOR_OFFSET rows are behind.
        cursor: str | None = None
        walked = 0
        pages = 0
        while walked < DEEP_CURSOR_OFFSET and pages < DEEP_MAX_PAGES:
            path = "/api/v1/traces?limit=50" + (f"&cursor={cursor}" if cursor else "")
            payload, _ = get_json(client, path)
            items = payload.get("items") or []
            if not items:
                break
            walked += len(items)
            pages += 1
            cursor = payload.get("next_cursor")
            if cursor is None:
                break
        if cursor is None:
            raise HarnessError(f"table exhausted after {walked} rows; cannot build a deep cursor")
        deep = timed_gets(client, f"/api/v1/traces?limit=50&cursor={cursor}", 100)
        metrics = timed_gets(
            client,
            "/api/v1/metrics?metrics=requests,latency,errors,queue&range=15m",
            100,
        )
    finally:
        client.close()
        stop_server(proc)
    traces = stats(shallow)
    traces_deep = stats(deep)
    metrics_15m = stats(metrics)
    worst_trace_p95 = max(traces["p95"], traces_deep["p95"])
    series = {"traces": traces, "traces_deep": traces_deep, "metrics_15m": metrics_15m}
    budgets = {
        "traces": {
            "limit": _BUDGET_LIMITS_MS["traces"],
            "unit": "ms",
            "value": worst_trace_p95,
            "pass": worst_trace_p95 <= _BUDGET_LIMITS_MS["traces"],
            "note": (
                f"worst of shallow p95 {traces['p95']} ms and deep-cursor p95"
                f" {traces_deep['p95']} ms at offset {walked} ({pages} keyset pages walked)"
            ),
        },
        "metrics_15m": {
            "limit": _BUDGET_LIMITS_MS["metrics_15m"],
            "unit": "ms",
            "value": metrics_15m["p95"],
            "pass": metrics_15m["p95"] <= _BUDGET_LIMITS_MS["metrics_15m"],
            "note": "GET /api/v1/metrics?metrics=requests,latency,errors,queue&range=15m",
        },
    }
    return {
        "series": series,
        "budgets": budgets,
        "scalars": {
            "traces_at_start": traces_at_start,
            "traces_at_end": db_trace_count(),
            "deep_cursor_offset": walked,
            "deep_cursor_pages": pages,
        },
    }


def run_static() -> dict:
    """Static asset budget: fresh GET p95 plus a conditional re-request that must return 304."""
    chunks = sorted((REPO_ROOT / "web" / "out" / "_next" / "static" / "chunks").glob("*.js"))
    if not chunks:
        raise HarnessError("web/out has no static chunks; build the UI first (web-check)")
    asset_path = f"/_next/static/chunks/{chunks[0].name}"
    proc = start_server("bench-static.log")
    client = Client(BENCH_PORT)
    try:
        status, _, raw, warm_headers = client.request("GET", asset_path)
        if status != 200:
            raise HarnessError(f"GET {asset_path} -> {status}: {raw[:200]!r}")
        etag = warm_headers.get("etag")
        if not etag:
            raise HarnessError(f"GET {asset_path} served no ETag; 304 revalidation unmeasurable")
        fresh: list[float] = []
        conditional: list[float] = []
        not_modified = 0
        for _ in range(3):  # warm-up discards (binary body: no JSON decoding here)
            status, _, _, _ = client.request("GET", asset_path)
            if status != 200:
                raise HarnessError(f"GET {asset_path} -> {status} during warm-up")
        for _ in range(100):
            status, elapsed_ms, _, _ = client.request("GET", asset_path)
            if status != 200:
                raise HarnessError(f"GET {asset_path} -> {status} during measurement")
            fresh.append(elapsed_ms)
        for _ in range(100):
            status, elapsed_ms, _, _ = client.request(
                "GET", asset_path, headers={"If-None-Match": etag}
            )
            conditional.append(elapsed_ms)
            if status == 304:
                not_modified += 1
    finally:
        client.close()
        stop_server(proc)
    asset = stats(fresh)
    revalidated = stats(conditional)
    ok = asset["p95"] <= _BUDGET_LIMITS_MS["static"] and not_modified == 100
    note = f"{asset_path}; conditional re-requests: {not_modified}/100 returned 304"
    return {
        "series": {"static_asset": asset, "static_conditional_304": revalidated},
        "budgets": {
            "static": {
                "limit": _BUDGET_LIMITS_MS["static"],
                "unit": "ms",
                "value": asset["p95"],
                "pass": ok,
                "note": note,
            }
        },
        "scalars": {"conditional_304": not_modified, "conditional_requests": 100},
    }


def _sse_connect(
    index: int,
    outputs: list[dict],
    socks: list[socket.socket],
    selector: selectors.BaseSelector,
) -> str | None:
    """Connect stream client ``index``; register it with the stamper or return its code."""
    request = (
        f"GET /api/v1/stream HTTP/1.1\r\nHost: 127.0.0.1:{BENCH_PORT}\r\n"
        "Accept: text/event-stream\r\n\r\n"
    ).encode()
    sock = socket.create_connection(("127.0.0.1", BENCH_PORT), timeout=10)
    sock.sendall(request)
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise HarnessError(f"sse client {index}: connection closed before headers")
        head += chunk
    head, _, rest = head.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    if status != 200:
        body = rest
        content_length = 0
        for line in head.split(b"\r\n")[1:]:
            key, _, value = line.partition(b":")
            if key.strip().lower() == b"content-length":
                content_length = int(value.strip())
        while len(body) < content_length:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
        sock.close()
        try:
            code = json.loads(body)["error"]["code"]
        except (ValueError, KeyError, TypeError):
            code = f"http_{status}"
        return str(code)
    out: dict = {"hello": None, "pulses": []}
    outputs.append(out)
    # The header read already owns any bytes past the terminator (hello rides the same
    # segments): stamp them here - closest to arrival - then hand the fd to the stamper.
    out["_buf"] = _sse_feed(b"", out, rest)
    selector.register(sock, selectors.EVENT_READ, out)
    socks.append(sock)
    return None


def _sse_feed(buf: bytes, out: dict, data: bytes) -> bytes:
    """Append ``data`` to ``buf``; stamp every complete frame; return the partial tail."""
    buf += data
    while b"\n\n" in buf:
        frame, buf = buf.split(b"\n\n", 1)
        name = ""
        for line in frame.split(b"\n"):
            if line.startswith(b"event: "):
                name = line[7:].strip().decode()
                break
        now = time.monotonic()
        if name == "hello" and out["hello"] is None:
            out["hello"] = now
        elif name == "pulse":
            out["pulses"].append(now)
    return buf


def _sse_stamp(selector: selectors.BaseSelector, stop: threading.Event) -> None:
    """The one stamping thread: epoll over every stream fd, one clock, one GIL consumer.

    Replaces the per-client reader threads (wave-2.5 part 3): 20 Python threads
    arbitrating the GIL after their own recv wakeups added a 1-3 ms straggler tail to
    the max-min-of-20 metric (standalone stamp jitter p50 0.785 / max 3.288 ms); a
    single selector loop stamps each frame the moment its fd reports readable.
    """
    try:
        while not stop.is_set():
            for key, _ in selector.select(timeout=0.5):
                try:
                    data = key.fileobj.recv(8192)
                except OSError:
                    data = b""
                if not data:
                    # EOF or reset: this client sends nothing more - stop watching it,
                    # or level-triggered epoll would spin on the dead fd.
                    try:
                        selector.unregister(key.fileobj)
                    except (KeyError, OSError):
                        pass
                    continue
                out = key.data
                out["_buf"] = _sse_feed(out["_buf"], out, data)
    except OSError:
        pass  # a socket died mid-select; the stop flag ends the loop anyway
    finally:
        selector.close()


def run_sse() -> dict:
    """Tick cost: hello-anchored spread of each pulse wave across the connected clients."""
    proc = start_server("bench-sse.log")
    outputs: list[dict] = []
    socks: list[socket.socket] = []
    rejections: list[str] = []
    selector = selectors.DefaultSelector()
    stop_stamp = threading.Event()
    stamp = threading.Thread(
        target=_sse_stamp, args=(selector, stop_stamp), name="sse-stamp", daemon=True
    )
    stamp.start()
    try:
        for index in range(SSE_CLIENTS):
            code = _sse_connect(index, outputs, socks, selector)
            if code is not None:
                rejections.append(code)
        if not outputs:
            raise HarnessError(f"no SSE clients connected (rejections: {rejections})")
        time.sleep(SSE_WINDOW_S)
    finally:
        stop_stamp.set()
        stamp.join(timeout=3)  # stop before closing: the stamper never touches dead fds
        for sock in socks:
            try:
                sock.close()
            except OSError:
                pass
        stop_server(proc)
    # Registered clients that never receive the hello frame are starved, not dead: the ASGI
    # bridge runs each stream's producer as a lifetime-bound thread on the event-loop default
    # executor (min(32, cpu+4) workers - 8 on this 4-cpu box), so only the first `executor`
    # streams ever get a byte past the 200 headers. They are counted, excluded from the wave
    # stats and they fail the 20-client precondition: recorded, not fixed (baseline rule).
    executor_workers = min(32, (os.cpu_count() or 1) + 4)
    fed = [out for out in outputs if out["hello"] is not None]
    starved = len(outputs) - len(fed)
    if not fed:
        raise HarnessError("no registered SSE client saw the hello frame")
    min_pulses = min(len(out["pulses"]) for out in fed)
    if min_pulses <= SSE_SKIP_WAVES:
        raise HarnessError(
            f"only {min_pulses} pulse waves in {SSE_WINDOW_S}s; need > {SSE_SKIP_WAVES}"
        )
    waves: list[float] = []
    for wave in range(SSE_SKIP_WAVES, min_pulses):
        relative = [out["pulses"][wave] - out["hello"] for out in fed]
        waves.append((max(relative) - min(relative)) * 1000.0)
    tick = stats(waves)
    registered = len(outputs)
    note = (
        "per wave: max-min arrival delta across data-fed clients, each client's clock "
        "anchored on its own hello frame; first 2 waves skipped"
    )
    if len(fed) < SSE_CLIENTS:
        note += (
            f"; only {len(fed)}/{SSE_CLIENTS} clients received stream data - {starved} of"
            f" {registered} registered clients starved after HTTP 200 (stream producers are"
            f" lifetime-bound threads on the {executor_workers}-worker event-loop default"
            f" executor) and {len(rejections)} connections were rejected"
            f" {sorted(set(rejections))} at hub max_clients=16; the 20-client precondition"
            " is unmeetable on stock code (recorded, not fixed)"
        )
    pass_ = tick["p95"] <= _BUDGET_LIMITS_MS["sse_tick"] and len(fed) == SSE_CLIENTS
    return {
        "series": {"sse_tick": tick},
        "budgets": {
            "sse_tick": {
                "limit": _BUDGET_LIMITS_MS["sse_tick"],
                "unit": "ms",
                "value": tick["p95"],
                "pass": pass_,
                "note": note,
            }
        },
        "scalars": {
            "clients_requested": SSE_CLIENTS,
            "clients_registered": registered,
            "clients_data_fed": len(fed),
            "clients_starved": starved,
            "clients_rejected": len(rejections),
            "rejection_codes": sorted(set(rejections)),
            "stream_executor_workers": executor_workers,
            "waves_measured": len(waves),
            "window_s": SSE_WINDOW_S,
        },
    }


_SUBCOMMANDS = {
    "overhead": run_overhead,
    "throughput": run_throughput,
    "api": run_api,
    "static": run_static,
    "sse": run_sse,
}
_ALL_ORDER = ("overhead", "throughput", "api", "static", "sse")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench.py",
        description=(
            "Measure the docs/performance.md section 1 bench budgets against a seeded database:"
            " each subcommand prints a p50/p90/p95/p99/max/n table and writes"
            " bench/results/<timestamp>.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py all\n"
            "  LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/bench.py api\n"
            "  .venv/bin/python scripts/bench.py overhead --strict\n"
            "Spawns its own engineless server on LW_BENCH_PORT (default 8091) and stops it.\n"
            "exit codes: 0 measured, 1 harness error (or any budget breach with --strict)"
        ),
    )
    parser.add_argument(
        "subcommand",
        choices=(*_SUBCOMMANDS, "all"),
        help="series to measure; 'all' runs overhead, throughput, api, static, sse in order",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any budget verdict fails (CI runs this at 2x budget)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    runs = _ALL_ORDER if args.subcommand == "all" else (args.subcommand,)
    series: dict[str, dict] = {}
    budgets: dict[str, dict] = {}
    scalars: dict = {}
    try:
        for name in runs:
            payload = _SUBCOMMANDS[name]()
            print_table(f"bench {name}", payload["series"])
            print_verdicts(payload["budgets"])
            series.update(payload["series"])
            budgets.update(payload["budgets"])
            scalars.update(payload["scalars"])
    except HarnessError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 1
    path = budget_check.write_result(
        {
            "harness": "bench",
            "subcommand": args.subcommand,
            "timestamp": stamp,
            "environment": budget_check.environment(),
            "series": series,
            "budgets": budgets,
            "scalars": scalars,
        },
        stamp,
    )
    print(f"bench: wrote {path.relative_to(REPO_ROOT)}")
    if args.strict and any(not budget["pass"] for budget in budgets.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
