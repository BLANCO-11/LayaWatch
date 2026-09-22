"""Engine request lifecycle: trace, auth, body parse, dispatch, response close.

``instrument(router, recorder, db_path)`` wraps ``router.dispatch`` with the observability
contract from docs/observability-model.md section 2 and docs/architecture.md section 4.
Requests outside ``engine_paths`` pass through untouched - no trace, no spans, no SQLite.
Inside the engine paths one trace opens per request (its id is the X-Request-Id the server
already assigned) and records spans in documented order: ``http.receive``, ``auth.verify``,
``body.parse``, the handler's engine spans, ``response.send``, with an ``error`` event
inserted on any failure. Every path ends in ``Context.finish`` - a crash in a handler can
lose a response, never a trace.

Two trace-closing modes, both covered by tests:

- default: the wrapper's ``finally`` emits ``response.send`` and finishes the trace before
  returning the response (the docs/architecture.md section 4 diagram order: close, reply);
- ``close_on_sent=True``: ``response.send`` enters before the wrapper returns and the
  response carries an ``on_sent`` hook that ``http/server.py``'s write path invokes after
  the body reaches the socket, so span and trace cover the actual write (section 1 bounds
  the trace at "response write"). The ``finally`` still closes everything if the hook is
  never invoked, so no trace can be lost either way.

``ThreadLocalSpans`` is the recorder proxy injected into the engine adapter at construction:
adapter spans (``lang.detect``, ``queue.wait``, ``forward``, ...) land in whichever trace is
active on the calling thread, and are no-ops outside a trace.
"""
from __future__ import annotations

import json
import traceback
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable

from layawatch.engine.keys import auth_armed, load_pepper, verify_key
from layawatch.http.router import Router
from layawatch.http.types import HttpError, Request, Response, error_response
from layawatch.log import get_logger
from layawatch.obs.recorder import Context, Recorder, current_context
from layawatch.store.db import connect

AUTH_CODE = "missing_or_invalid_credential"
INTERNAL_CODE = "internal_error"

_logger = get_logger("http")


class _NullSpan:
    """Context manager returned by ThreadLocalSpans outside a trace (stateless, shared)."""

    __slots__ = ()

    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


_NULL_SPAN = _NullSpan()


class ThreadLocalSpans:
    """Recorder proxy handed to the engine adapter: spans join the active request trace.

    Delegates ``span``/``event``/``set`` to whichever ``Context`` the recorder bound to the
    calling thread; outside a recorded context every call is a no-op (``span`` returns a
    null context manager), so adapter work on a helper thread or before the first request
    records nothing instead of raising. The surface mirrors the adapter's ``spans`` seam
    (``engine/adapter.py``: span + set + event).
    """

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def span(self, name: str, input: Any = None, output: Any = None, **attrs: Any) -> Any:
        ctx = current_context()
        return _NULL_SPAN if ctx is None else ctx.span(name, input, output, **attrs)

    def event(self, name: str, input: Any = None, output: Any = None, **attrs: Any) -> Any:
        ctx = current_context()
        return None if ctx is None else ctx.event(name, input, output, **attrs)

    def set(self, **fields: Any) -> None:
        ctx = current_context()
        if ctx is not None:
            ctx.set(**fields)


def instrument(
    router: Router,
    recorder: Recorder,
    db_path: str | Path,
    *,
    engine_paths: tuple[str, ...] = ("/predict", "/route"),
    close_on_sent: bool = False,
) -> Callable[[Request], Response]:
    """Return a dispatch callable with the engine request lifecycle (architecture.md 4).

    ``close_on_sent=True`` defers trace closure to the response's ``on_sent`` hook, which the
    server's write path calls after writing the body; otherwise the ``finally`` closes the
    trace on return. Either way the trace finishes exactly once.
    """
    engine = frozenset(engine_paths)

    def handle(request: Request) -> Response:
        if request.path not in engine:
            return router.dispatch(request)  # passthrough: no trace, no spans, no SQLite

        ctx = recorder.start(
            route=request.path,
            method=request.method,
            request_id=request.request_id,
            client_ip=request.client_ip,
        )
        response: Response | None = None
        status = 500
        attached = False
        phase = "http.receive"
        try:
            try:
                with ctx.span(
                    "http.receive",
                    method=request.method,
                    path=request.path,
                    remote=request.client_ip,
                    content_length=len(request.body),
                ):
                    pass  # the server already consumed the body; this span bounds intake
                phase = "auth.verify"
                _verify_auth(ctx, request, db_path)
                phase = "body.parse"
                _parse_body(ctx, request)
                phase = "handler"
                response = router.dispatch(request)
                if response.status >= 400:
                    _record_failure(ctx, response, phase)
            except HttpError as exc:
                # Auth and body failures raise HttpError directly; the envelope, error event
                # and trace error fields are rendered here, inside the lifecycle.
                response = error_response(exc, request.request_id)
                ctx.event("error", code=exc.code, message=exc.message, where=phase)
                ctx.set(error_code=exc.code, error_message=exc.message)
            except Exception as exc:
                _logger.error(
                    f"unhandled error handling {request.method} {request.path}: "
                    f"{traceback.format_exc()}"
                )
                response = error_response(
                    HttpError(500, INTERNAL_CODE, "internal server error"),
                    request.request_id,
                )
                ctx.event("error", code=INTERNAL_CODE, message="internal server error", where=phase)
                ctx.set(error_code=INTERNAL_CODE, error_message=str(exc))
            status = response.status
            if close_on_sent:
                _attach_send_hook(ctx, response)
                attached = True
            return response
        finally:
            if not attached:
                # Default mode, and the crash-proof fallback for close_on_sent: emit the
                # send span and finish here, so no handler crash can lose the trace.
                with ctx.span(
                    "response.send",
                    status=status,
                    bytes=len(response.body) if response is not None else 0,
                ):
                    pass
                ctx.finish(status)

    return handle


def _attach_send_hook(ctx: Context, response: Response) -> None:
    """Enter ``response.send`` now; the server's write path invokes ``response.on_sent``.

    The span closes when the hook runs - after the header and body have been written - so
    its duration (and the trace's) covers the socket write. ``ExitStack`` lets the span be
    entered here and exited later; ``Context.finish`` and ``ExitStack.close`` are both
    idempotent, so a double invocation is harmless.
    """
    stack = ExitStack()
    stack.enter_context(
        ctx.span("response.send", status=response.status, bytes=len(response.body))
    )

    def sent() -> None:
        stack.close()
        ctx.finish(response.status)

    response.on_sent = sent


def _verify_auth(ctx: Context, request: Request, db_path: str | Path) -> None:
    """Verify the credential through a short-lived connection; a refusal raises inside the span.

    Unarmed deployments skip key work entirely but still record the span (``scheme="none"``,
    ``ok=True``) and ``meta.armed``. On success the key id lands in the span attribute, the
    ``auth.verify`` span and ``client_key_id`` - never the presented secret, which is never
    stored anywhere.
    """
    presented, scheme = _presented_credential(request)
    conn = connect(db_path)
    try:
        armed = auth_armed(conn)
        key_id = None
        if armed and presented is not None:
            key_id = verify_key(conn, presented, load_pepper(Path(db_path).parent))
    finally:
        conn.close()
    ok = not armed or key_id is not None
    ctx.set_meta(armed=armed)
    attrs: dict[str, Any] = {"scheme": scheme if armed else "none", "ok": ok}
    if key_id is not None:
        attrs["key_id"] = key_id
        ctx.set(client_key_id=key_id)
    with ctx.span("auth.verify", **attrs):
        if not ok:
            raise HttpError(401, AUTH_CODE, "missing or invalid credential")


def _presented_credential(request: Request) -> tuple[str | None, str]:
    """``(credential, scheme)`` from ``X-API-Key`` or ``Authorization: Bearer`` (api-reference 1).

    Header keys arrive lowercased from the server; an absent or blank credential yields
    ``(None, "none")`` so the span records what the request actually presented.
    """
    header = request.headers.get("x-api-key")
    if header is not None and header.strip():
        return header.strip(), "api_key"
    authorization = request.headers.get("authorization", "")
    if authorization[:7].lower() == "bearer " and authorization[7:].strip():
        return authorization[7:].strip(), "api_key"
    return None, "none"


def _parse_body(ctx: Context, request: Request) -> None:
    """Parse the JSON body and attach it as ``request.parsed``; invalid input marks the span.

    The body is parsed twice - an untimed probe that computes the span attributes, then a
    timed pure re-run inside ``body.parse`` - following the adapter's documented pattern
    (engine/adapter.py class docstring): attributes must exist before a span opens, and
    ``json.loads`` is pure and costs microseconds. Invalid JSON re-raises the 400
    ``invalid_json`` HttpError from ``Request.json`` inside the span, marking it error.
    """
    try:
        parsed = request.json()
    except HttpError:
        with ctx.span("body.parse", state_bytes=len(request.body)):
            request.json()  # deterministic re-raise: the span observes the failure
        raise
    attrs: dict[str, Any] = {"state_bytes": len(request.body)}
    questions = parsed.get("questions") if isinstance(parsed, dict) else None
    if isinstance(questions, (dict, list)):
        attrs["question_count"] = len(questions)
    if isinstance(parsed, dict):
        if parsed.get("model") is not None:
            attrs["model_param"] = str(parsed["model"])
        if parsed.get("lang") is not None:
            attrs["lang_param"] = str(parsed["lang"])
    with ctx.span("body.parse", **attrs):
        parsed = request.json()  # timed pass; pure, see docstring
    request.parsed = parsed  # consumed by the engine handlers (api/engine.py)


def _record_failure(ctx: Context, response: Response, where: str) -> None:
    """Record the error event and trace error fields from a router-rendered envelope."""
    code, message = _envelope_error(response)
    ctx.event("error", code=code, message=message, where=where)
    ctx.set(error_code=code, error_message=message)


def _envelope_error(response: Response) -> tuple[str, str]:
    """``(code, message)`` from an error envelope, with a status-derived fallback."""
    try:
        error = json.loads(response.body)["error"]
        return str(error["code"]), str(error.get("message", ""))
    except (TypeError, ValueError, KeyError):
        return f"http_{response.status}", ""
