"""Resource budget gate for LayaWatch: ``rss``, ``disk``, ``coldstart``, ``idle-cpu``, ``all``.

Budgets and the harness contract come from ``docs/performance.md`` sections 1 and 2::

    rss        running server RSS (no model weights)      <= 120 MB
    disk       state directory size (LAYA_STATE_DIR)      <= 200 MB
    coldstart  launch to first 200 on GET /healthz        <= 3000 ms
    idle-cpu   server CPU% with no clients over 60 s      <  2 %

Usage::

    .venv/bin/python scripts/budget_check.py rss
    LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py all
    .venv/bin/python scripts/budget_check.py disk --json

Every run prints one table row per budget (``name value budget PASS/FAIL``) and writes the same
row to ``bench/results/budget-<name>-<timestamp>.json`` for cross-phase comparison; ``--json``
additionally prints a flat array of ``{"name", "value", "budget", "unit", "pass"}`` objects to
stdout (the path notice goes to stderr). ``all`` runs every subcommand in the table order above
and stops at the first breach.
Exit codes: 0 when every checked budget passes, 1 on a breach or an impossible measurement, 2 on
usage errors.

All four measurements read ONE database: ``LAYA_STATE_DIR`` (default ``.``, matching
``layawatch.config.Config``), the same directory ``scripts/seed.py`` seeds and ``scripts/bench.py``
targets. The ``rss``, ``coldstart`` and ``idle-cpu`` measurements launch a throwaway child on
``LAYA_PORT=8099`` against that state directory and SIGTERM it in a ``finally`` block; child
output lands in ``budget-<name>.log`` next to the state database. The child boots engineless:
the launch command nulls ``sys.modules["laya"]`` before ``layawatch/__main__.py`` probes
``importlib.util.find_spec("laya")``, so the deterministic FakeAdapter serves (healthz reports
``device=fake``) and no checkpoint loads during a measurement (P1-I3 warns against parallel
engine loads; the RSS budget excludes model weights). ``disk`` counts the same
``LAYA_STATE_DIR`` and never counts the Hugging Face model cache, even when a deployment places
that cache inside the state directory. An ``image`` subcommand is deliberately absent; a later
phase adds it.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PORT = 8099
#: Engineless boot: null the module before __main__.py's find_spec probe so the fake adapter
#: serves and no checkpoint can load while a measurement runs (see the module docstring).
_BOOT = (
    "import sys; sys.modules['laya'] = None;"
    " from layawatch.__main__ import main; raise SystemExit(main())"
)
RSS_BUDGET_MB = 120.0
DISK_BUDGET_MB = 200.0
COLDSTART_BUDGET_MS = 3000.0
IDLE_CPU_BUDGET_PCT = 2.0  # strict "less than" per docs/performance.md section 1
IDLE_WINDOW_S = 60.0
IDLE_SAMPLE_S = 5.0
STARTUP_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.01
WARMUP_SETTLE_S = 0.5
_COMMANDS = ("rss", "disk", "coldstart", "idle-cpu")
_HEADER = f"{'name':<10}{'value':>12}{'budget':>10}  result"


class HarnessError(Exception):
    """Measurement impossible: port busy, server died, or a /proc entry unreadable."""


class StartupTimeout(HarnessError):
    """The child never answered ``/healthz`` inside the startup window."""

    def __init__(self, message: str, elapsed_ms: float) -> None:
        super().__init__(message)
        self.elapsed_ms = elapsed_ms


@dataclass(frozen=True)
class Row:
    """One budget result; ``passed`` is already decided against the doc threshold."""

    name: str
    value: float
    budget: float
    unit: str
    passed: bool

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": round(self.value, 3),
            "budget": self.budget,
            "unit": self.unit,
            "pass": self.passed,
        }


def _fmt(value: float, unit: str) -> str:
    if unit == "MB":
        return f"{value:.1f} MB"
    if unit == "ms":
        return f"{value:.0f} ms"
    return f"{value:.2f} %"


def _fmt_budget(budget: float, unit: str) -> str:
    return f"{budget:g} {unit}"


def _table_line(row: Row) -> str:
    verdict = "PASS" if row.passed else "FAIL"
    value = _fmt(row.value, row.unit)
    budget = _fmt_budget(row.budget, row.unit)
    return f"{row.name:<10}{value:>12}{budget:>10}  {verdict}"


def _tail(log_path: Path, lines: int = 20) -> str:
    try:
        with open(log_path, errors="replace") as fh:
            content = fh.read().splitlines()
    except OSError:
        return "(no log)"
    return "\n".join(content[-lines:]) or "(empty log)"


def state_dir() -> Path:
    """The one state directory every measurement reads (``LAYA_STATE_DIR``, default ``.``)."""
    return Path(os.environ.get("LAYA_STATE_DIR") or ".")


def environment() -> dict[str, object]:
    """Compact host block recorded with every result (evidence, cross-phase comparison)."""
    return {
        "host": socket.gethostname(),
        "python": platform.python_version(),
        "cpus": os.cpu_count(),
        "loadavg": [round(value, 2) for value in os.getloadavg()],
        "sqlite": sqlite3.sqlite_version,
    }


def write_result(payload: dict, stem: str) -> Path:
    """Write one harness result JSON under ``bench/results/<stem>.json``; returns the path."""
    target = REPO_ROOT / "bench" / "results" / f"{stem}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return target


def spawn_server(
    *,
    state_dir: Path,
    port: int,
    log_name: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[bytes], float]:
    """Start an engineless ``python -m layawatch`` child on ``port``; return it and its launch time.

    ``LAYA_STATE_DIR=state_dir`` makes the child serve the same seeded database every harness
    reads; ``log_name`` lands next to that database. ``_BOOT`` forces the deterministic
    FakeAdapter (no checkpoint ever loads - P1-I3). ``extra_env`` overlays the child
    environment (e.g. ``LAYWATCH_RECORD=0`` for the overhead A/B).
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise HarnessError(f"port {port} already has a listener; stop it before measuring")
    env = os.environ.copy()
    env["LAYA_PORT"] = str(port)
    env["LAYA_STATE_DIR"] = str(state_dir)
    if extra_env:
        env.update(extra_env)
    log_path = state_dir / log_name
    launched = time.perf_counter()
    with open(log_path, "wb") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-c", _BOOT],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    return proc, launched


def stop_server(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM the child, escalating to SIGKILL only if it refuses to exit."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def wait_serving(
    proc: subprocess.Popen[bytes],
    launched: float,
    log_path: Path,
    *,
    port: int,
) -> float:
    """Poll ``GET /healthz`` on ``port`` until 200; return elapsed ms since ``launched``."""
    health_url = f"http://127.0.0.1:{port}/healthz"
    deadline = time.perf_counter() + STARTUP_TIMEOUT_S
    while True:
        if proc.poll() is not None:
            raise HarnessError(
                f"server exited with code {proc.returncode} before serving; "
                f"log tail:\n{_tail(log_path)}"
            )
        try:
            with urllib.request.urlopen(health_url, timeout=1) as resp:
                if resp.status == 200:
                    return (time.perf_counter() - launched) * 1000
        except (OSError, http.client.HTTPException):
            pass  # connection refused / transient bad response: keep polling
        if time.perf_counter() >= deadline:
            raise StartupTimeout(
                f"no 200 from {health_url} within {STARTUP_TIMEOUT_S:.0f}s; "
                f"log tail:\n{_tail(log_path)}",
                (time.perf_counter() - launched) * 1000,
            )
        time.sleep(POLL_INTERVAL_S)


def _spawn(name: str) -> tuple[subprocess.Popen[bytes], float]:
    """Budget child for ``name``: the shared state directory on ``PORT`` (see ``spawn_server``)."""
    target = state_dir()
    return spawn_server(
        state_dir=target, port=PORT, log_name=f"budget-{name}.log"
    )


def _rss_mb(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError as exc:
        raise HarnessError(f"cannot read /proc/{pid}/status: {exc}") from exc
    raise HarnessError(f"no VmRSS line in /proc/{pid}/status")


def _cpu_ticks(pid: int) -> int:
    """utime+stime of ``pid`` in clock ticks (fields 14 and 15 of /proc/<pid>/stat)."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
    except OSError as exc:
        raise HarnessError(f"cannot read /proc/{pid}/stat: {exc}") from exc
    fields = data[data.rfind(")") + 2 :].split()
    if len(fields) < 13:
        raise HarnessError(f"malformed /proc/{pid}/stat")
    return int(fields[11]) + int(fields[12])


def _hf_cache_roots() -> list[Path]:
    """Resolved directories that hold the Hugging Face model cache (never disk-budgeted)."""
    roots = [Path.home() / ".cache" / "huggingface"]
    for var in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        raw = os.environ.get(var)
        if raw:
            roots.append(Path(raw).expanduser())
    return [root.resolve() for root in roots]


def _is_under(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def _dir_size(root: Path) -> int:
    """Recursive byte size of regular files under ``root``, excluding the HF model cache."""
    if not root.is_dir():
        return 0

    def on_walk_error(exc: OSError) -> None:
        raise HarnessError(f"cannot read {exc.filename}: {exc.strerror}")

    roots = _hf_cache_roots()
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=on_walk_error):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not _is_under(here / d, roots)]
        if _is_under(here, roots):
            continue
        for filename in filenames:
            try:
                total += os.stat(here / filename).st_size
            except OSError:
                continue  # file vanished mid-walk (SQLite churn) or is a broken symlink
    return total


def _measure_rss() -> Row:
    """RSS of a freshly started server after one served request (performance.md section 1)."""
    proc, launched = _spawn("rss")
    try:
        wait_serving(proc, launched, state_dir() / "budget-rss.log", port=PORT)
        time.sleep(WARMUP_SETTLE_S)  # "after warmup": let the served request settle
        mb = _rss_mb(proc.pid)
    finally:
        stop_server(proc)
    return Row("rss", mb, RSS_BUDGET_MB, "MB", mb <= RSS_BUDGET_MB)


def _measure_disk() -> Row:
    """Recursive size of the shared ``LAYA_STATE_DIR`` (HF model cache excluded), in MB."""
    root = state_dir()
    mb = _dir_size(root) / (1024 * 1024)
    return Row("disk", mb, DISK_BUDGET_MB, "MB", mb <= DISK_BUDGET_MB)


def _measure_coldstart() -> Row:
    """Launch to first 200 on ``/healthz``; never serving within 30 s is itself a breach."""
    proc, launched = _spawn("coldstart")
    try:
        try:
            elapsed_ms = wait_serving(
                proc, launched, state_dir() / "budget-coldstart.log", port=PORT
            )
        except StartupTimeout as exc:
            print(f"budget_check: {exc}", file=sys.stderr)
            return Row("coldstart", exc.elapsed_ms, COLDSTART_BUDGET_MS, "ms", False)
    finally:
        stop_server(proc)
    return Row("coldstart", elapsed_ms, COLDSTART_BUDGET_MS, "ms", elapsed_ms <= COLDSTART_BUDGET_MS)


def _measure_idle_cpu() -> Row:
    """Average CPU% of the serving child over a 60 s window with no clients (strict < 2 %)."""
    proc, launched = _spawn("idle-cpu")
    try:
        wait_serving(proc, launched, state_dir() / "budget-idle-cpu.log", port=PORT)
        time.sleep(WARMUP_SETTLE_S)
        clock_hz = os.sysconf("SC_CLK_TCK")
        ticks_start = _cpu_ticks(proc.pid)
        window_start = time.perf_counter()
        deadline = window_start + IDLE_WINDOW_S
        while True:  # sliced sleep keeps the child watched and Ctrl-C responsive
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(IDLE_SAMPLE_S, remaining))
            if proc.poll() is not None:
                raise HarnessError("server exited during the idle window")
        window_end = time.perf_counter()
        # Counters are cumulative: window average = (end - start) ticks / hz / elapsed.
        ticks_end = _cpu_ticks(proc.pid)
    finally:
        stop_server(proc)
    cpu_seconds = (ticks_end - ticks_start) / clock_hz
    pct = cpu_seconds / (window_end - window_start) * 100
    return Row("idle-cpu", pct, IDLE_CPU_BUDGET_PCT, "%", pct < IDLE_CPU_BUDGET_PCT)


_MEASURES = {
    "rss": _measure_rss,
    "disk": _measure_disk,
    "coldstart": _measure_coldstart,
    "idle-cpu": _measure_idle_cpu,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="budget_check.py",
        description="Assert the LayaWatch resource budgets from docs/performance.md section 1.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  .venv/bin/python scripts/budget_check.py rss\n"
            "  LAYA_STATE_DIR=/tmp/lw-bench .venv/bin/python scripts/budget_check.py all\n"
            "  .venv/bin/python scripts/budget_check.py disk --json\n"
            "Every run also writes bench/results/budget-<name>-<timestamp>.json.\n"
            "exit codes: 0 all checked budgets pass, 1 breach or impossible measurement, 2 usage"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        help="print a flat JSON array of results instead of the table",
    )
    for name in _COMMANDS:
        subparsers.add_parser(name, help=f"measure the {name} budget", parents=[common])
    subparsers.add_parser(
        "all",
        help="run every budget in table order, stopping at the first breach",
        parents=[common],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rows: list[Row] = []
    failure: str | None = None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    host = environment()
    if not args.json:
        print(_HEADER)
    try:
        names = _COMMANDS if args.command == "all" else (args.command,)
        for name in names:
            row = _MEASURES[name]()
            rows.append(row)
            path = write_result(
                {
                    "harness": "budget_check",
                    "subcommand": name,
                    "timestamp": stamp,
                    "environment": host,
                    "budget": row.as_json(),
                },
                f"budget-{name}-{stamp}",
            )
            print(f"budget_check: wrote {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            if not args.json:
                print(_table_line(row))
            if not row.passed:
                break
    except HarnessError as exc:
        failure = str(exc)
    if args.json:
        print(json.dumps([row.as_json() for row in rows], indent=2))
    if failure is not None:
        print(f"budget_check: {failure}", file=sys.stderr)
        return 1
    return 0 if all(row.passed for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
