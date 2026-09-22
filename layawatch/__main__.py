"""LayaWatch entrypoint: ``python -m layawatch`` (``--migrate-only`` applies migrations and exits)."""
from __future__ import annotations

import argparse
import sys

from layawatch.api.health import add_health_routes
from layawatch.config import Config, ConfigError
from layawatch.engine.adapter import FakeAdapter
from layawatch.http.router import Router
from layawatch.http.server import run
from layawatch.http.static import mount as mount_static
from layawatch.log import configure, get_logger
from layawatch.store.db import SchemaTooNewError, connect, migrate


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

    if args.migrate_only:
        conn.close()
        return 0

    # Phase 0 serves healthz from the fake adapter; the real laya adapter arrives in Phase 1.
    adapter = FakeAdapter()
    adapter.load(["english"])

    router = Router()
    add_health_routes(router, adapter, auth_enabled=False)
    mount_static(router, cfg.web_root)

    log.info(f"layawatch starting on http://{cfg.bind}:{cfg.port} (web_root={cfg.web_root})")
    try:
        run(cfg, router)
    finally:
        conn.close()
        log.info("layawatch stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
