"""POST /predict and POST /route: shape validation, adapter call, trace fields.

Contract from docs/api-reference.md section 4: the body is ``{state, questions, model?,
task?, lang?, temperature?}``; both endpoints answer the adapter's JSON shape and record a
trace. Engine rejections map the way the pre-v0.1.0 legacy server mapped them - an english-only
refusal becomes 422, any other ``ValueError`` becomes 400, everything else escapes to the router's
500 ``internal_error`` without leaking exception text into the body.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from layawatch.engine.adapter import EnglishOnlyError
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.obs.recorder import current_context

if TYPE_CHECKING:
    from layawatch.engine.adapter import EngineAdapter
    from layawatch.http.router import Router


def add_engine_routes(router: Router, adapter: EngineAdapter) -> None:
    """Register ``POST /predict`` and ``POST /route``; other methods fall to the router's 405.

    Handlers assume the middleware parsed the body into ``request.parsed`` and opened a
    trace on this thread (architecture.md section 4): validation failures raise ``HttpError``
    for the middleware/router to render and record, and successful results update the
    trace's model/route_reason/lang fields. The response's ``X-Request-Id`` equals the
    trace id with no special-casing - the router stamps it from the request.
    """

    def predict(request: Request) -> Response:
        payload = _validated(request)
        _record_request(request, payload)
        result = _guard(
            lambda: adapter.predict(
                payload["state"],
                payload["questions"],
                model=_param(payload, "model"),
                task=_param(payload, "task"),
                lang=_param(payload, "lang"),
            )
        )
        _record_result(result)
        return json_response(result)

    def route(request: Request) -> Response:
        payload = _validated(request)
        _record_request(request, payload)
        decision = _guard(lambda: adapter.route(payload["state"], payload["questions"]))
        _record_result(decision)
        return json_response(decision)

    router.add("POST", "/predict", predict)
    router.add("POST", "/route", route)


def _validated(request: Request) -> dict[str, Any]:
    """Require the documented top-level shape: a JSON object with dict ``state`` and ``questions``."""
    payload = request.parsed
    if not isinstance(payload, dict):
        raise HttpError(
            400, "invalid_request", "request body must be a JSON object", details={"field": "body"}
        )
    if not isinstance(payload.get("state"), dict):
        raise HttpError(
            400, "invalid_request", "'state' must be a JSON object", details={"field": "state"}
        )
    if not isinstance(payload.get("questions"), dict):
        raise HttpError(
            400,
            "invalid_request",
            "'questions' must be a JSON object",
            details={"field": "questions"},
        )
    return payload


def _param(payload: dict[str, Any], name: str) -> Any:
    """Truthy body override (the pre-v0.1.0 server passed ``model``/``task`` through verbatim)."""
    value = payload.get(name)
    return value if value else None


def _guard(call: Callable[[], dict]) -> dict:
    """Map engine rejections to HTTP the way the pre-v0.1.0 server did: 422 refusal, 400 ValueError.

    ``EnglishOnlyError`` subclasses ``ValueError``, so it is caught first: a non-English
    state in an english-only deployment is a 422 ``english_only`` carrying the legacy
    ``detection`` payload in ``details``. Other engine ``ValueError``s (unknown model name,
    bad task) become 400 ``invalid_request`` with the engine's message. Anything else - a
    crashed forward pass - escapes to the router's generic 500 with no text leaked.
    """
    try:
        return call()
    except EnglishOnlyError as exc:
        details = {"detection": exc.detection} if exc.detection else None
        raise HttpError(422, "english_only", str(exc), details=details) from None
    except ValueError as exc:
        raise HttpError(400, "invalid_request", str(exc)) from None


def _record_request(request: Request, payload: dict[str, Any]) -> None:
    """Trace attributes known at request time: body size and question count (obs-model 3)."""
    ctx = current_context()
    if ctx is None:
        return
    ctx.set(state_bytes=len(request.body), question_count=len(payload["questions"]))


def _record_result(result: dict) -> None:
    """Trace fields from the adapter result; RealAdapter also writes these via spans.set."""
    ctx = current_context()
    if ctx is None:
        return
    ctx.set(
        model=result.get("model"),
        route_reason=result.get("route_reason") or result.get("reason"),
        lang=result.get("lang"),
    )
