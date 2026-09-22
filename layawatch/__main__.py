"""LayaWatch entrypoint: ``python -m layawatch`` (``--migrate-only`` applies migrations and exits)."""
from __future__ import annotations

import argparse
import importlib.util
import sys
import threading
import time

from layawatch.api.auth import add_auth_routes
from layawatch.api.engine import add_engine_routes
from layawatch.api.health import add_health_routes, add_meta_routes, add_root_route
from layawatch.api.legacy import add_legacy_routes, deprecation_counts
from layawatch.api.logs import add_logs_routes
from layawatch.api.metrics import add_metrics_routes
from layawatch.api.models import add_model_routes
from layawatch.api.settings import add_settings_routes
from layawatch.api.stream import hub
from layawatch.api.traces import add_trace_routes
from layawatch.app import create_app
from layawatch.config import Config, ConfigError
from layawatch.engine.adapter import FakeAdapter, RealAdapter
from layawatch.engine.keys import auth_armed
from layawatch.engine.legacy_state import import_once
from layawatch.http.middleware import ThreadLocalSpans, instrument
from layawatch.http.router import Router
from layawatch.http.server import run
from layawatch.http.static import mount as mount_static
from layawatch.log import configure, get_logger, set_sink, set_trace_provider
from layawatch.obs.recorder import Recorder
from layawatch.store import queries
from layawatch.store.db import SchemaTooNewError, connect, migrate
from layawatch.store.retention import run_forever
from layawatch.store.writer import Writer


def _rss_mb() -> float:
    """Current process RSS in MiB (zero when unreadable)."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _fanout_log(entry: dict, writer: Writer) -> None:
    """Offer a log entry to the writer queue and the SSE hub (sinks must not block)."""
    writer.enqueue_log(entry)
    hub.publish_log(entry)


def _publish_flushed(traces: list) -> None:
    """Adapt the writer ``on_flush`` hook (kept Trace objects) to SSE summaries."""
    for trace in traces:
        hub.publish_trace(
            {
                "id": trace.id,
                "ts_start": trace.ts_start,
                "duration_ms": trace.duration_ms,
                "route": trace.route,
                "method": trace.method,
                "status": trace.status,
                "model": trace.model,
                "route_reason": trace.route_reason,
                "tags": trace.tags,
                "meta": trace.meta,
            }
        )


def _pulse_provider(db_path: str, writer: Writer):
    """SSE pulse closure: 5-minute KPI strip plus process and queue health."""

    def provider() -> dict:
        conn = connect(db_path)
        try:
            summary = queries.metrics_summary(conn, 300.0)
        finally:
            conn.close()
        counters = writer.counters()
        return {
            "requests_per_s": summary.get("requests_per_s", 0.0),
            "errors_5m": summary.get("errors", 0),
            "p50": summary.get("p50", 0.0),
            "p95": summary.get("p95", 0.0),
            "queue_ms": summary.get("queue_ms", 0.0),
            "rss_mb": _rss_mb(),
            "dropped_total": counters.get("obs_dropped_total", 0),
            "queue_depth": counters.get("write_queue_depth", 0),
        }

    return provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="layawatch",
        description="Self-hosted observability console for the laya decision engine.",
    )
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="apply pending database migrations and exit",
    )
    args = parser.parse_args(argv)

    try:
        cfg = Config.load()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    configure(cfg.log_level, cfg.log_ring_size)
    log = get_logger("layawatch")

    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(cfg.db_path)
    try:
        applied = migrate(conn)
    except SchemaTooNewError as exc:
        log.error(str(exc))
        return 3
    if applied:
        log.info(f"migrations applied: {applied}")
    else:
        log.info("database schema up to date")

    imported = import_once(conn, cfg.state_dir)
    log.info(f"legacy api_keys.json import: {imported} keys imported")

    if args.migrate_only:
        conn.close()
        return 0

    # Storage pipeline: writer thread owns all writes; log lines ride the same queue.
    # The on_flush hook publishes trace summaries to SSE (writer tick cadence).
    writer = Writer(
        str(cfg.db_path), queue_max=cfg.write_queue_max, on_flush=_publish_flushed
    )
    writer.start()  # enqueue works before this, nothing commits without it
    set_sink(lambda entry, _w=writer: _fanout_log(entry, _w))

    recorder = Recorder(
        ring_size=cfg.ring_traces,
        sample_rate=cfg.trace_sample,
        enabled=cfg.record,
        capture=cfg.capture_payloads,
        payload_max=cfg.payload_max,
        on_trace=writer.enqueue,
    )
    set_trace_provider(recorder.trace_provider())

    # Retention timer: build rollups, prune to caps, checkpoint, guarded VACUUM.
    stop_retention = threading.Event()
    threading.Thread(
        target=run_forever,
        kwargs={
            "db_path": str(cfg.db_path),
            "stop_event": stop_retention,
            "interval": 60.0,
            "max_traces": cfg.retention_traces,
            "max_logs": cfg.log_ring_size,
            "max_age_days": cfg.retention_days,
            "queue_idle": lambda: writer.counters().get("write_queue_depth", 0) == 0,
        },
        name="layawatch-retention",
        daemon=True,
    ).start()

    # Engine: the real adapter when the laya package is installed, the deterministic fake
    # otherwise (healthz then reports device=fake; checkpoints load lazily on first use).
    spans = ThreadLocalSpans(recorder)
    if importlib.util.find_spec("laya") is not None:
        adapter = RealAdapter(
            spans=spans,
            device=cfg.device,
            models=cfg.models,
            english_only=cfg.english_only,
            state_dir=cfg.state_dir,
        )
        log.info(f"engine: real adapter (models={','.join(cfg.models)}, device={cfg.device})")
    else:
        adapter = FakeAdapter()
        adapter.load(["english"])
        log.warning("laya package not importable; serving with the fake engine (device=fake)")

    started_at = time.time()
    router = Router()
    add_health_routes(router, adapter, auth_enabled=auth_armed(conn))
    add_root_route(router, cfg.web_root)
    add_meta_routes(
        router,
        config=cfg,
        started_at=started_at,
        counters=writer.counters,
        sse_clients=hub.client_count,
        deprecations=deprecation_counts,
    )
    add_trace_routes(router, cfg.db_path)
    add_metrics_routes(router, cfg.db_path)
    add_logs_routes(router, cfg.db_path)
    add_settings_routes(router, cfg, cfg.db_path)
    add_auth_routes(router, config=cfg, db_path=cfg.db_path)
    add_model_routes(router, adapter, cfg, cfg.db_path, on_change=hub.publish_model)
    add_engine_routes(router, adapter)
    dispatch = instrument(router, recorder, cfg.db_path, close_on_sent=True, spans=spans)
    hub.mount(router)
    hub.attach(
        tick_s=float(cfg.stream_tick),
        ring_size=cfg.ring_traces,
        pulse_provider=_pulse_provider(str(cfg.db_path), writer),
        counter_provider=writer.counters,
    )
    add_legacy_routes(router)
    mount_static(router, cfg.web_root)
    conn.close()

    log.info(f"layawatch starting on http://{cfg.bind}:{cfg.port} (web_root={cfg.web_root})")
    app = create_app(dispatch, max_body_bytes=cfg.max_body_bytes)
    try:
        run(cfg, app)
    finally:
        stop_retention.set()
        writer.stop(timeout=5.0)
        hub.stop()
        set_sink(None)
        set_trace_provider(None)
        log.info("layawatch stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
