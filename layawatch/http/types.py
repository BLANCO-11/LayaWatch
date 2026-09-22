"""Shared HTTP types for the LayaWatch standard-library server.

Handlers receive a :class:`Request` and return a :class:`Response`. Raising :class:`HttpError`
inside a handler renders the JSON error envelope from ``docs/api-reference.md`` section 2:
``{"error": {"code", "message", "details"?}}``. The router adds ``X-Request-Id`` to every response.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit


class HttpError(Exception):
    """Rendered as the JSON error envelope by the router."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]  # keys lowercased
    body: bytes
    request_id: str  # 8 hex chars
    client_ip: str

    @classmethod
    def build(
        cls,
        method: str,
        target: str,
        headers: dict[str, str],
        body: bytes,
        request_id: str,
        client_ip: str,
    ) -> "Request":
        """Parse a raw request target (``/path?query``) into a Request."""
        parts = urlsplit(target)
        return cls(
            method=method.upper(),
            path=parts.path or "/",
            query=parse_query(parts.query),
            headers=headers,
            body=body,
            request_id=request_id,
            client_ip=client_ip,
        )

    def json(self) -> Any:
        """Parse the body as JSON; invalid input raises ``HttpError 400 invalid_json``."""
        if not self.body:
            raise HttpError(400, "invalid_json", "request body is empty")
        try:
            return _json.loads(self.body)
        except ValueError as exc:
            raise HttpError(400, "invalid_json", f"invalid JSON body: {exc}") from None

    def param(self, name: str, default: str | None = None) -> str | None:
        """First value of a query parameter, or ``default``."""
        values = self.query.get(name)
        return values[0] if values else default


def parse_query(query_string: str) -> dict[str, list[str]]:
    """Parse a query string, keeping blank values."""
    return parse_qs(query_string, keep_blank_values=True)


@dataclass
class Response:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def json_response(
    data: Any,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    body = _json.dumps(data, separators=(",", ":")).encode("utf-8")
    out = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        out.update(headers)
    return Response(status=status, headers=out, body=body)


def text_response(
    text: str,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    headers: dict[str, str] | None = None,
) -> Response:
    out = {"Content-Type": content_type}
    if headers:
        out.update(headers)
    return Response(status=status, headers=out, body=text.encode("utf-8"))


def empty_response(status: int = 204, headers: dict[str, str] | None = None) -> Response:
    return Response(status=status, headers=dict(headers or {}), body=b"")


def error_response(err: HttpError, request_id: str) -> Response:
    envelope: dict[str, Any] = {"code": err.code, "message": err.message}
    if err.details:
        envelope["details"] = err.details
    return json_response(
        {"error": envelope},
        status=err.status,
        headers={"X-Request-Id": request_id},
    )
