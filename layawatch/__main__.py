"""LayaWatch entrypoint: ``python -m layawatch`` (``--migrate-only`` applies migrations and exits)."""
from __future__ import annotations

import argparse
import importlib.util
import sys
import threading

from layawatch.api.engine import add_engine_routes
from layawatch.api.health import add_health_routes
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
from layawatch.store.db import SchemaTooNewError, connect, migrate
from layawatch.store.retention import run_forever
from layawatch.store.writer import Writer


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
    writer = Writer(str(cfg.db_path), queue_max=cfg.write_queue_max)
    writer.start()
    set_sink(writer.enqueue_log)

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

    router = Router()
    add_health_routes(router, adapter, auth_enabled=auth_armed(conn))
    add_engine_routes(router, adapter)
    dispatch = instrument(router, recorder, cfg.db_path, close_on_sent=True)
    mount_static(router, cfg.web_root)
    conn.close()

    log.info(f"layawatch starting on http://{cfg.bind}:{cfg.port} (web_root={cfg.web_root})")
    try:
        run(cfg, router, dispatch=dispatch)
    finally:
        stop_retention.set()
        writer.stop(timeout=5.0)
        set_sink(None)
        set_trace_provider(None)
        log.info("layawatch stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
