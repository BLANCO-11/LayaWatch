"""Playground surface (docs/api-reference.md section 12, plan phase-6 tasks 3-4).

``GET /api/v1/playground/templates`` answers the three built-in templates - department,
urgency, churn - with their questions, to any authenticated role (viewer+).
``POST /api/v1/playground/run`` is admin+ (``playground.run``) and mirrors
``POST /predict`` in-process: same body shape ``{state, questions, model?, task?, lang?,
temperature?}``, same rejection mapping (422 english_only, 400 invalid_request), same
response shape - recorded as one trace with ``meta.source = "playground"`` so the run
appears in the Traces list within one stream tick and its ``X-Request-Id`` (equal to the
trace id) links the playground status badge to the detail page. ``lang`` and
``temperature`` are accepted and echoed into the ``body.parse`` span attributes exactly
as the middleware records them for ``/predict``; the adapter call itself passes
``model``/``task`` only, matching the engine handler verbatim.

The run handler owns its trace lifecycle because only ``/predict`` and ``/route`` sit on
the middleware's engine path (``http/middleware.py``): the gate runs first so a 401/403
never enters the ring, then ``recorder.start`` binds the thread-local context - adapter
spans (``forward``, ``model.load``) join it through ``current_context`` - and a ``finally``
emits ``response.send`` and finishes the trace, crash-proof like the middleware's
non-hook path. ``auth.verify`` is deliberately absent: this path authenticates a console
session in the gate, never an engine key. Unexpected handler exceptions record an
``internal_error`` event and re-raise for the router to render and log.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from layawatch.auth.sessions import gate
from layawatch.engine.adapter import EnglishOnlyError
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.config import Config
    from layawatch.http.router import Router
    from layawatch.obs.recorder import Context, Recorder

#: The three built-in templates (section 12); question specs are the canonical engine
#: fixtures the playground page seeds its editor with (type/instructions/criteria shapes).
_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "department",
        "name": "Department",
        "questions": {
            "department": {
                "type": "choice",
                "instructions": "Which department should handle this request?",
                "criteria": {
                    "billing": "invoices, payments, refunds",
                    "technical": "bugs, outages, system errors",
                    "sales": "pricing, new contracts",
                    "other": "everything else",
                },
            }
        },
    },
    {
        "id": "urgency",
        "name": "Urgency",
        "questions": {
            "urgency": {
                "type": "score",
                "instructions": "How urgent is this request?",
                "criteria": ["not urgent", "soon", "critical deadline or blocking issue"],
            }
        },
    },
    {
        "id": "churn",
        "name": "Churn",
        "questions": {
            "churn_risk": {
                "type": "noul",
                "instructions": "Does the user threaten to cancel or leave?",
            }
        },
    },
)


def add_playground_routes(
    router: Router,
    adapter: Any,
    *,
    config: Config,
    db_path: str | Path,
    recorder: Recorder,
) -> None:
    """Register the section 12 playground endpoints on ``router``."""

    def templates(request: Request) -> Response:
        unknown = next(iter(request.query), None)
        if unknown is not None:
            raise HttpError(400, "invalid_filter", f"unknown query parameter {unknown!r}")
        conn = connect(db_path)
        try:
            gate(request, conn, config, db_path, None)
        finally:
            conn.close()
        return json_response({"items": list(_TEMPLATES)})

    def run(request: Request) -> Response:
        conn = connect(db_path)
        try:
            gate(request, conn, config, db_path, "playground.run")
        finally:
            conn.close()
        ctx = recorder.start(
            route=request.path,
            method=request.method,
            request_id=request.request_id,
            client_ip=request.client_ip,
        )
        ctx.set_meta(source="playground")
        response: Response | None = None
        status = 500
        try:
            with ctx.span(
                "http.receive",
                method=request.method,
                path=request.path,
                remote=request.client_ip,
                content_length=len(request.body),
            ):
                pass  # intake bound, same phase the engine middleware opens first
            payload = _probe(request, ctx)
            ctx.set(state_bytes=len(request.body), question_count=len(payload["questions"]))
            result = _guard(
                lambda: adapter.predict(
                    payload["state"],
                    payload["questions"],
                    model=_param(payload, "model"),
                    task=_param(payload, "task"),
                )
            )
            ctx.set(
                model=result.get("model"),
                route_reason=result.get("route_reason") or result.get("reason"),
                lang=result.get("lang"),
            )
            response = json_response(result)
            status = 200
        except HttpError as exc:
            ctx.event("error", code=exc.code, message=exc.message, where="handler")
            ctx.set(error_code=exc.code, error_message=exc.message)
            status = exc.status
            raise
        except Exception:
            ctx.event("error", code="internal_error", message="internal server error",
                      where="handler")
            status = 500
            raise
        finally:
            with ctx.span(
                "response.send",
                status=status,
                bytes=len(response.body) if response is not None else 0,
            ):
                pass
            ctx.finish(status)
        return response

    router.add("GET", "/api/v1/playground/templates", templates)
    router.add("POST", "/api/v1/playground/run", run)


def _probe(request: Request, ctx: Context) -> dict[str, Any]:
    """Validate the body with the middleware's ``body.parse`` pattern: attributes first
    (state bytes, question count, model/lang params), then the timed parse inside the span.
    """
    probe = request.json()  # untimed probe; json.loads is pure, the span re-runs it
    attrs: dict[str, Any] = {"state_bytes": len(request.body)}
    questions = probe.get("questions") if isinstance(probe, dict) else None
    if isinstance(questions, dict):
        attrs["question_count"] = len(questions)
    if isinstance(probe, dict):
        if probe.get("model") is not None:
            attrs["model_param"] = str(probe["model"])
        if probe.get("lang") is not None:
            attrs["lang_param"] = str(probe["lang"])
    with ctx.span("body.parse", **attrs):
        return _validated(request)  # timed pass; mirrors api/engine._validated


def _validated(request: Request) -> dict[str, Any]:
    """The documented ``/predict`` top-level shape (api/engine._validated, verbatim)."""
    payload = request.json()
    if not isinstance(payload, dict):
        raise HttpError(
            400, "invalid_request", "request body must be a JSON object",
            details={"field": "body"},
        )
    if not isinstance(payload.get("state"), dict):
        raise HttpError(
            400, "invalid_request", "'state' must be a JSON object",
            details={"field": "state"},
        )
    if not isinstance(payload.get("questions"), dict):
        raise HttpError(
            400, "invalid_request", "'questions' must be a JSON object",
            details={"field": "questions"},
        )
    return payload


def _param(payload: dict[str, Any], name: str) -> Any:
    """Truthy body override (legacy/serve.py passes ``model``/``task`` through verbatim)."""
    value = payload.get(name)
    return value if value else None


def _guard(call: Callable[[], dict]) -> dict:
    """Engine rejections -> HTTP exactly like ``api/engine._guard`` (422 refusal, 400 value)."""
    try:
        return call()
    except EnglishOnlyError as exc:
        details = {"detection": exc.detection} if exc.detection else None
        raise HttpError(422, "english_only", str(exc), details=details) from None
    except ValueError as exc:
        raise HttpError(400, "invalid_request", str(exc)) from None
