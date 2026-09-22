"""Route registration: liveness (``/healthz``), service info (``/``) and meta (``/api/v1/meta``)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

import layawatch
from layawatch.http import static
from layawatch.http.types import HttpError, Request, Response, json_response

if TYPE_CHECKING:
    from collections.abc import Iterable

    from layawatch.config import Config
    from layawatch.http.router import Router


class Adapter(Protocol):
    """Duck-typed engine adapter surface used by the health payload."""

    def loaded(self) -> Iterable[str]:
        ...

    def device(self) -> str:
        ...


def add_health_routes(router: Router, adapter: Adapter, *, auth_enabled: bool = False) -> None:
    """Register ``GET /healthz`` returning status, loaded models, device, and auth state."""

    def healthz(_request: Request) -> Response:
        return json_response(
            {
                "status": "ok",
                "models": list(adapter.loaded()),
                "device": adapter.device(),
                "auth_enabled": auth_enabled,
            }
        )

    router.add("GET", "/healthz", healthz)


def add_root_route(router: Router, web_root: Path) -> None:
    """Register ``GET /``: the JSON service info for API clients, the static UI for browsers.

    The JSON shape answers only when ``Accept`` explicitly includes ``application/json``
    and not ``text/html``; anything else delegates to ``static.resolve``, which serves
    ``web_root/index.html`` when the export exists and the build page when it is absent.
    Register before ``static.mount`` so the exact route wins over the ``/*`` catch-all.
    """

    def root(request: Request) -> Response:
        accept = request.headers.get("accept", "")
        media = {part.split(";")[0].strip().lower() for part in accept.split(",")}
        if "application/json" in media and "text/html" not in media:
            return json_response(
                {
                    "service": "layawatch",
                    "version": layawatch.__version__,
                    "docs": "https://github.com/BLANCO-11/LayaWatch",
                    "ui": "/",
                }
            )
        response = static.resolve("/", web_root)
        if response is None:
            raise HttpError(404, "not_found", "not found")
        return response

    router.add("GET", "/", root)


#: Config keys exposed by ``/api/v1/meta``: never a path (state_dir, db_path, web_root) and
#: never a credential (admin_token, session_secret, bootstrap_owner).
_SAFE_CONFIG: tuple[str, ...] = (
    "bind",
    "port",
    "device",
    "models",
    "english_only",
    "record",
    "api",
    "trust_proxy",
    "trace_sample",
    "capture_payloads",
    "payload_max",
    "retention_traces",
    "retention_days",
    "ring_traces",
    "stream_tick",
    "max_body_bytes",
    "socket_timeout",
    "log_level",
)


def safe_config_snapshot(config: Config) -> dict:
    """The :data:`_SAFE_CONFIG`-reduced config mapping shared by ``/api/v1/meta`` and
    ``GET /api/v1/settings``: every safe field's current value, so neither read view can
    leak a path or a credential."""
    return {name: getattr(config, name) for name in _SAFE_CONFIG}


def add_meta_routes(
    router: Router,
    *,
    config: Config,
    started_at: float,
    counters: Callable[[], dict],
    sse_clients: Callable[[], int],
    deprecations: Callable[[], dict],
) -> None:
    """Register ``GET /api/v1/meta``: version, uptime, the safe config snapshot and counters.

    ``started_at`` is epoch seconds from process start; ``uptime_s`` is recomputed per
    request. ``config`` is reduced to the :data:`_SAFE_CONFIG` allowlist so no path or
    secret can leak, ``counters`` (the writer snapshot) spreads at the top level beside
    them, and ``deprecation`` carries the live legacy-shim counts.
    """

    def meta(_request: Request) -> Response:
        return json_response(
            {
                "version": layawatch.__version__,
                "uptime_s": round(time.time() - started_at, 3),
                "started_at": started_at,
                "db_size_bytes": _db_size(config.db_path),
                "rss_mb": _rss_mb(),
                "sse_clients": sse_clients(),
                "config": safe_config_snapshot(config),
                "deprecation": deprecations(),
                **counters(),
            }
        )

    router.add("GET", "/api/v1/meta", meta)


def _db_size(path: Path) -> int:
    """Size of the SQLite file in bytes, or 0 before the first write."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _rss_mb() -> float:
    """Resident set size in MiB from ``/proc/self/status``; 0.0 where unavailable."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        return 0.0
    return 0.0
