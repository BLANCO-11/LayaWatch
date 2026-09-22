"""Model admin surface: ``GET /api/v1/models`` plus load/unload mutations.

Contract from docs/api-reference.md section 11: the list answers with ``{loaded, available,
default, device, rss_mb}`` where ``loaded`` comes from the adapter (``loaded()`` and
``device()`` never construct the engine, so a GET can never trigger a checkpoint download)
and ``available``/``default`` are the configured checkpoints - ``["english"]`` under
``english_only``, mirroring what ``RealAdapter`` will boot with. ``default`` carries the same
adjusted list because that is what legacy/serve.py exposed as ``default: MODELS`` for the old
admin page through the section 14 shim; the two keys may diverge once a wider engine catalog
exists.

Mutations accept the section 11 bodies (``POST /api/v1/models/load`` and
``POST /api/v1/models/unload`` with ``{models: [...]}``) plus ``POST /api/v1/models`` with
``{action: "load"|"unload", models: [...]}`` - the body legacy/serve.py's admin page sends
and the target the section 14 shim maps onto, so old clients keep working. Rejections map per
the wave contract: adapter ``ValueError`` (unknown checkpoint) is ``400 invalid_request``,
the in-flight ``RuntimeError`` is ``409 load_in_progress``, ``EngineMemoryError`` is
``503 insufficient_memory`` carrying the message in ``details``, and a bad ``action`` or
``models`` field is ``400 invalid_request``. A successful action is audited
(``model.load``/``model.unload``, target = comma-joined names) on a short-lived connection,
answers with the post-action loaded list, and invokes ``on_change`` so the SSE stream can
emit its ``model`` event.

Authentication and role gates arrive in Phase 3: these endpoints work without credentials
until then. Only ``/predict`` and ``/route`` are middleware engine paths, so model admin
calls create no trace.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from layawatch.engine.adapter import EngineMemoryError
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store.db import connect
from layawatch.store.queries import insert_audit

if TYPE_CHECKING:
    from pathlib import Path

    from layawatch.config import Config
    from layawatch.engine.adapter import EngineAdapter
    from layawatch.http.router import Router


def add_model_routes(
    router: Router,
    adapter: EngineAdapter,
    config: Config,
    db_path: str | Path,
    *,
    on_change: Callable[[dict], None] | None = None,
) -> None:
    """Register the section 11 model endpoints; ``on_change`` publishes the SSE model event.

    ``db_path`` is opened only by the mutating handlers, short-lived and closed in a
    ``finally`` (the ``_verify_auth`` precedent); the list handler touches no database.
    """

    def list_models(_request: Request) -> Response:
        available = _available(config)
        return json_response(
            {
                "loaded": list(adapter.loaded()),
                "available": available,
                "default": list(available),
                "device": adapter.device(),
                "rss_mb": _rss_mb(),
            }
        )

    def mutate(request: Request, forced_action: str | None = None) -> Response:
        payload = request.json()
        if not isinstance(payload, dict):
            raise HttpError(
                400,
                "invalid_request",
                "request body must be a JSON object",
                details={"field": "body"},
            )
        action = forced_action
        if action is None:
            action = payload.get("action")
            if action not in ("load", "unload"):
                raise HttpError(
                    400,
                    "invalid_request",
                    "'action' must be load or unload",
                    details={"field": "action"},
                )
        models = payload.get("models")
        if (
            not isinstance(models, list)
            or not models
            or not all(isinstance(name, str) and name for name in models)
        ):
            raise HttpError(
                400,
                "invalid_request",
                "'models' must be a non-empty list of strings",
                details={"field": "models"},
            )
        _apply(adapter, action, models)
        conn = connect(db_path)
        try:
            insert_audit(conn, "api", f"model.{action}", target=",".join(models))
            conn.commit()
        finally:
            conn.close()
        loaded = list(adapter.loaded())
        if on_change is not None:
            on_change({"loaded": loaded, "action": action})
        return json_response({"loaded": loaded})

    router.add("GET", "/api/v1/models", list_models)
    router.add("POST", "/api/v1/models", lambda request: mutate(request))
    router.add("POST", "/api/v1/models/load", lambda request: mutate(request, "load"))
    router.add("POST", "/api/v1/models/unload", lambda request: mutate(request, "unload"))


def _available(config: Config) -> list[str]:
    """Configured checkpoints, narrowed to ``["english"]`` under english_only (adapter boot set)."""
    return ["english"] if config.english_only else list(config.models)


def _apply(adapter: EngineAdapter, action: str, models: list[str]) -> None:
    """Run one lifecycle action with the wave's error mapping (memory before in-flight)."""
    try:
        if action == "load":
            adapter.load(models)
        else:
            adapter.unload(models)
    except EngineMemoryError as exc:
        raise HttpError(
            503, "insufficient_memory", str(exc), details={"message": str(exc)}
        ) from None
    except RuntimeError as exc:
        raise HttpError(409, "load_in_progress", str(exc)) from None
    except ValueError as exc:
        raise HttpError(400, "invalid_request", str(exc)) from None


def _rss_mb() -> float:
    """Resident set size in MiB from ``/proc/self/status``; 0.0 where unavailable.

    Same source as legacy/serve.py's ``_rss_mb`` and ``api/health.py``'s copy: section 11's
    ``rss_mb`` is process-wide, not per-model.
    """
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        return 0.0
    return 0.0
