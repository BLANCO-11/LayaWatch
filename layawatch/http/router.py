"""Path-to-handler table with method dispatch and the JSON error envelope.

Patterns are exact paths (``/healthz``) or trailing-``*`` prefixes (``/api/*``). An exact match
beats a prefix match, the longest matching prefix wins, and equal-length prefix ties go to
registration order. A mounted ``/*`` catches every otherwise-unmatched path; without it an unknown
path is a ``404 not_found`` envelope. ``dispatch`` never raises: a handler's ``HttpError`` renders
as the envelope, any other exception becomes a generic ``500 internal_error`` whose detail is
logged instead of returned, and every response carries an ``X-Request-Id``.
"""
from __future__ import annotations

import traceback
from typing import Callable

from layawatch.http.types import (
    HttpError,
    Request,
    Response,
    empty_response,
    error_response,
)
from layawatch.log import get_logger

Handler = Callable[[Request], Response]

_logger = get_logger("http")


class Router:
    """Routes ``(path, method)`` to a handler per the module contract."""

    def __init__(self) -> None:
        self._exact: dict[str, dict[str, Handler]] = {}
        self._prefixes: dict[str, dict[str, Handler]] = {}

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        """Register ``handler`` for ``method`` on ``pattern`` (exact path or trailing-``*``)."""
        self._methods_for(pattern)[method.upper()] = handler

    def routes(self) -> list[tuple[str, str]]:
        """Every registered ``(method, pattern)`` pair, sorted - the API surface as built.

        ``api/openapi.py`` diffs its operation table against this so a new route cannot go
        undocumented silently.
        """
        pairs = [
            (method, pattern)
            for table in (self._exact, self._prefixes)
            for pattern, methods in table.items()
            for method in methods
        ]
        return sorted(pairs, key=lambda pair: (pair[1], pair[0]))

    def dispatch(self, request: Request) -> Response:
        """Route ``request``. Never raises; every response carries ``X-Request-Id``, and only
        statuses other than 204/304 carry ``Content-Length``."""
        try:
            response = self._route(request)
        except HttpError as exc:
            response = error_response(exc, request.request_id)
        except Exception:
            _logger.error(
                f"unhandled exception handling {request.method} {request.path}: "
                f"{traceback.format_exc()}"
            )
            response = error_response(
                HttpError(500, "internal_error", "internal server error"),
                request.request_id,
            )
        response.headers["X-Request-Id"] = request.request_id
        if response.status in (204, 304):
            for name in [n for n in response.headers if n.lower() == "content-length"]:
                del response.headers[name]
        elif not any(name.lower() == "content-length" for name in response.headers):
            response.headers["Content-Length"] = str(len(response.body))
        return response

    def _route(self, request: Request) -> Response:
        methods = self._match(request.path)
        if methods is None:
            return error_response(
                HttpError(404, "not_found", f"no route for {request.method} {request.path}"),
                request.request_id,
            )
        method = request.method.upper()
        if method == "OPTIONS":
            handler = methods.get("OPTIONS")
            if handler is not None:
                return handler(request)
            return empty_response(204, {"Allow": _allow(methods)})
        if method == "HEAD":
            handler = methods.get("HEAD") or methods.get("GET")
            if handler is None:
                return _not_allowed(request, methods)
            response = handler(request)
            response.headers["Content-Length"] = response.headers.get(
                "Content-Length", str(len(response.body))
            )
            response.body = b""
            return response
        handler = methods.get(method)
        if handler is None:
            return _not_allowed(request, methods)
        return handler(request)

    def _methods_for(self, pattern: str) -> dict[str, Handler]:
        table = self._prefixes if pattern.endswith("*") else self._exact
        methods = table.get(pattern)
        if methods is None:
            methods = {}
            table[pattern] = methods
        return methods

    def _match(self, path: str) -> dict[str, Handler] | None:
        exact = self._exact.get(path)
        if exact is not None:
            return exact
        best: dict[str, Handler] | None = None
        best_length = -1
        for pattern, methods in self._prefixes.items():
            prefix = pattern[:-1]
            if len(prefix) > best_length and path.startswith(prefix):
                best, best_length = methods, len(prefix)
        return best


def _allow(methods: dict[str, Handler]) -> str:
    """Concrete methods plus HEAD when GET exists, plus OPTIONS, sorted for determinism."""
    allowed = set(methods)
    if "GET" in allowed:
        allowed.add("HEAD")
    allowed.add("OPTIONS")
    return ", ".join(sorted(allowed))


def _not_allowed(request: Request, methods: dict[str, Handler]) -> Response:
    response = error_response(
        HttpError(405, "method_not_allowed", f"method {request.method} not allowed"),
        request.request_id,
    )
    response.headers["Allow"] = _allow(methods)
    return response
