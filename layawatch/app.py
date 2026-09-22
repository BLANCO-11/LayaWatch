"""FastAPI application: ASGI adapter over the instrumented router dispatch.

uvicorn speaks ASGI; this module adapts each request to the http layer's ``Request``,
hands it to ``dispatch`` (router + middleware), and renders the ``Response`` back:
bytes with gzip for large JSON, the empty bodies of 204/304/HEAD, or an SSE stream.
The wire guards the old socket handler owned live here - 411/413 body framing,
inbound ``X-Request-Id`` validation - and ``on_sent`` fires after the body or stream
has actually been written, so traces still bound on ``response.send``.
"""
from __future__ import annotations

import asyncio
import gzip
import queue
import re
import secrets
import threading
import traceback
from typing import Any

import anyio
from fastapi import FastAPI
from fastapi import Request as AsgiRequest
from fastapi.responses import Response as RawResponse
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from layawatch.http.types import HttpError, Request, Response, error_response
from layawatch.log import get_logger

_logger = get_logger("http")

_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_BODY_REQUIRED = frozenset({"POST", "PUT", "PATCH"})  # DELETE needs no body (RFC 9110)
_GZIP_MIN = 1024
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_EOF: Any = object()


def _envelope(status: int, code: str, message: str, request_id: str) -> RawResponse:
    return _render(error_response(HttpError(status, code, message), request_id), request_id)


def _gzipped(headers: dict[str, str], body: bytes, accept: str, status: int) -> tuple[bytes, dict[str, str]] | None:
    if status in (204, 304) or len(body) <= _GZIP_MIN or "gzip" not in accept:
        return None
    if not headers.get("Content-Type", "").startswith("application/json"):
        return None
    if headers.get("Content-Encoding"):
        return None
    compressed = gzip.compress(body)
    out = dict(headers)
    out["Content-Encoding"] = "gzip"
    out["Vary"] = "Accept-Encoding"
    out["Content-Length"] = str(len(compressed))
    return compressed, out


def _render(resp: Response, request_id: str) -> RawResponse:
    headers = dict(resp.headers)
    if not any(key.lower() == "x-request-id" for key in headers):
        headers["X-Request-Id"] = request_id
    body = resp.body
    return RawResponse(
        content=body,
        status_code=resp.status,
        headers=headers,
    )


class _Writer:
    """Thread-safe SSE sink: the stream callable blocks on a full queue (backpressure)."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self.done = threading.Event()

    def write(self, data: Any) -> None:
        chunk = data.encode() if isinstance(data, str) else bytes(data)
        while True:
            if self._stop.is_set():
                raise BrokenPipeError("stream consumer gone")
            try:
                self._q.put(chunk, timeout=0.2)
                return
            except queue.Full:
                continue

    def flush(self) -> None:
        return None

    def read(self) -> Any:
        while True:
            try:
                return self._q.get(timeout=0.25)
            except queue.Empty:
                if self.done.is_set():
                    return _EOF

    def close(self) -> None:
        self._stop.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            return None


def create_app(dispatch: Any, *, max_body_bytes: int = 4194304) -> FastAPI:
    """Build the FastAPI app whose single catch-all route delegates to ``dispatch``."""
    app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.api_route("/{path:path}", methods=_METHODS)
    async def handle(asgi: AsgiRequest) -> Any:
        request_id = asgi.headers.get("x-request-id", "")
        if not _REQUEST_ID_RE.fullmatch(request_id):
            request_id = secrets.token_hex(4)
        client_ip = asgi.client.host if asgi.client else ""

        content_length = asgi.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                return _envelope(400, "invalid_content_length", "invalid Content-Length", request_id)
            if declared > max_body_bytes:
                return _envelope(
                    413,
                    "payload_too_large",
                    f"request body exceeds {max_body_bytes} bytes",
                    request_id,
                )
        elif asgi.method in _BODY_REQUIRED and "transfer-encoding" not in asgi.headers:
            return _envelope(411, "length_required", "Content-Length header is required", request_id)

        body = await asgi.body()
        raw_path = asgi.scope.get("raw_path") or asgi.scope.get("path", "/").encode()
        target = raw_path.decode("latin-1")
        query = asgi.scope.get("query_string", b"")
        if query:
            target = f"{target}?{query.decode('latin-1')}"
        request = Request.build(
            method=asgi.method,
            target=target,
            headers={key.lower(): value for key, value in asgi.headers.items()},
            body=body,
            request_id=request_id,
            client_ip=client_ip,
        )

        resp = await anyio.to_thread.run_sync(dispatch, request)

        if resp.stream is not None:
            return _stream_response(resp, request_id)

        headers = dict(resp.headers)
        if not any(key.lower() == "x-request-id" for key in headers):
            headers["X-Request-Id"] = request_id
        accept = asgi.headers.get("accept-encoding", "")
        gz = _gzipped(headers, resp.body, accept, resp.status)
        if gz is not None:
            body, headers = gz
        else:
            body = resp.body
            headers.pop("Content-Length", None) if resp.status in (204, 304) else None

        def fire() -> None:
            hook = getattr(resp, "on_sent", None)
            if callable(hook):
                try:
                    hook()
                except Exception:
                    _logger.error(f"on_sent hook failed:\n{traceback.format_exc()}")

        return RawResponse(
            content=body,
            status_code=resp.status,
            headers=headers,
            background=BackgroundTask(fire),
        )

    def _stream_response(resp: Response, request_id: str) -> StreamingResponse:
        headers = dict(resp.headers)
        if not any(key.lower() == "x-request-id" for key in headers):
            headers["X-Request-Id"] = request_id
        headers.pop("Content-Length", None)
        writer = _Writer()

        def produce() -> None:
            try:
                resp.stream(writer)  # type: ignore[misc]
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                _logger.error(f"stream callback failed:\n{traceback.format_exc()}")
            finally:
                writer.done.set()
                hook = getattr(resp, "on_sent", None)
                if callable(hook):
                    try:
                        hook()
                    except Exception:
                        _logger.error(f"on_sent hook failed:\n{traceback.format_exc()}")

        async def generate():
            task = asyncio.get_running_loop().run_in_executor(None, produce)
            try:
                while True:
                    chunk = await anyio.to_thread.run_sync(writer.read)
                    if chunk is _EOF:
                        break
                    yield chunk
            finally:
                writer.close()
                await task  # reap; producer exits once close() unblocks it

        return StreamingResponse(generate(), status_code=resp.status, headers=headers)

    return app
