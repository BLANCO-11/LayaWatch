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

#: 4.1 item 5: gzip decision memo per ``Accept-Encoding`` value. The header is
#: client-controlled, so the cache is capped and cleared rather than grown.
_GZIP_ACCEPT: dict[str, bool] = {}
_GZIP_ACCEPT_MAX = 64


def _gzip_acceptable(accept: str) -> bool:
    """Whether ``accept`` allows gzip; the answer is computed once per distinct value."""
    decision = _GZIP_ACCEPT.get(accept)
    if decision is None:  # miss (stored values are booleans, never None)
        if len(_GZIP_ACCEPT) >= _GZIP_ACCEPT_MAX:
            _GZIP_ACCEPT.clear()
        decision = "gzip" in accept
        _GZIP_ACCEPT[accept] = decision
    return decision


def _envelope(status: int, code: str, message: str, request_id: str) -> RawResponse:
    return _render(error_response(HttpError(status, code, message), request_id), request_id)


def _gzipped(headers: dict[str, str], body: bytes, accept: str, status: int) -> tuple[bytes, dict[str, str]] | None:
    # 1 KB floor first (4.1 item 5): small bodies never touch the cache or gzip.
    if status in (204, 304) or len(body) <= _GZIP_MIN:
        return None
    if not _gzip_acceptable(accept):
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


def _render(
    resp: Response,
    request_id: str,
    *,
    accept: str = "",
    background: BackgroundTask | None = None,
) -> RawResponse:
    """Serialize one response exactly once: bytes body in, bytes body out (4.1 item 2).

    Error envelopes and successful responses share this single rendering point. The
    body is already bytes (``json_response``/``text_response`` encoded it once at
    build time) and reaches the ASGI layer untouched - no decode, no second encode;
    only gzip, when the cached per-``Accept-Encoding`` decision and the 1 KB floor
    allow it, replaces it with one compressed byte string.
    """
    headers = dict(resp.headers)
    if not any(key.lower() == "x-request-id" for key in headers):
        headers["X-Request-Id"] = request_id
    body = resp.body
    if resp.status in (204, 304):
        headers.pop("Content-Length", None)
    elif accept:
        gz = _gzipped(headers, body, accept, resp.status)
        if gz is not None:
            body, headers = gz
    return RawResponse(
        content=body,
        status_code=resp.status,
        headers=headers,
        background=background,
    )


class _Writer:
    """Thread-safe SSE sink: the stream callable blocks on a full queue (backpressure).

    The consumer half is loop-native (4.5 wave-2.5 part 2): each producer write wakes
    the streaming generator through ``call_soon_threadsafe`` on one ``asyncio.Event``,
    so a delivered chunk costs a put plus a loop callback instead of a worker-thread
    round trip per yield (``anyio.to_thread.run_sync`` measured 90.5 us plus limiter
    traffic per chunk, x20 clients per tick).
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self.done = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None

    def bind(self) -> None:
        """Bind the consumer's event loop; call on the loop before the producer starts."""
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()

    def write(self, data: Any) -> None:
        if isinstance(data, bytes):
            chunk = data  # 4.1 item 2: already serialized - handed off, not re-copied
        elif isinstance(data, str):
            chunk = data.encode()  # the one serialization to bytes
        else:
            chunk = bytes(data)
        while True:
            if self._stop.is_set():
                raise BrokenPipeError("stream consumer gone")
            try:
                self._q.put(chunk, timeout=0.2)
                break
            except queue.Full:
                continue
        self._signal()

    def flush(self) -> None:
        return None

    async def read(self) -> Any:
        """Next chunk on the consumer's loop: non-blocking poll, then park on the event.

        Ordering keeps the sticky event honest: the poll and ``clear`` run without a
        yield, so a ``set`` either lands before the poll (the chunk is there) or after
        the ``wait`` begins (the wake is delivered) - no wakeup can be lost, and stale
        sets from already-consumed chunks are cleared while the queue reads empty.
        """
        while True:
            try:
                return self._q.get_nowait()
            except queue.Empty:
                if self.done.is_set():
                    return _EOF
            wake = self._wake
            assert wake is not None  # bind() runs before the producer thread exists
            wake.clear()
            await wake.wait()

    def finish(self) -> None:
        """Producer-side end of stream: mark done and wake a parked consumer."""
        self.done.set()
        self._signal()

    def _signal(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is None or wake is None:
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass  # event loop already closed: no consumer left to wake

    def close(self) -> None:
        self._stop.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            return None


async def _read_body(
    asgi: AsgiRequest, request_id: str, declared: int | None, cap: int
) -> bytes | RawResponse:
    """Read the request body in one bounded pass over the declared length (4.1 item 3).

    The common case consumes a single ``http.request`` message that already carries
    the whole declared body - returned as-is, no join, no copy. More messages are
    pulled only while bytes are still owed to the declared count. Undeclared
    (chunked) bodies are refused the moment the running total passes ``cap``, so an
    oversized upload is never buffered whole; ``Content-Length`` oversize is rejected
    by the caller's pre-check before any read at all.
    """
    message = await asgi.receive()
    chunk = message.get("body") or b""
    if declared is not None:
        if len(chunk) >= declared:
            return chunk[:declared]  # one-shot read of exactly the declared length
        parts = [chunk]
        total = len(chunk)
        while message.get("more_body") and total < declared:
            message = await asgi.receive()
            chunk = message.get("body") or b""
            parts.append(chunk)
            total += len(chunk)
        return b"".join(parts) if len(parts) > 1 else parts[0]
    # Undeclared length (chunked or no body): enforce the cap as bytes arrive.
    parts = [chunk]
    total = len(chunk)
    while total <= cap and message.get("more_body"):
        message = await asgi.receive()
        chunk = message.get("body") or b""
        parts.append(chunk)
        total += len(chunk)
    if total > cap:
        return _envelope(413, "payload_too_large", f"request body exceeds {cap} bytes", request_id)
    return b"".join(parts) if len(parts) > 1 else parts[0]


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
        declared: int | None = None
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                return _envelope(400, "invalid_content_length", "invalid Content-Length", request_id)
            if declared > max_body_bytes:
                # Pre-checked on Content-Length: refused before a single body byte is read.
                return _envelope(
                    413,
                    "payload_too_large",
                    f"request body exceeds {max_body_bytes} bytes",
                    request_id,
                )
        elif asgi.method in _BODY_REQUIRED and "transfer-encoding" not in asgi.headers:
            return _envelope(411, "length_required", "Content-Length header is required", request_id)

        body = await _read_body(asgi, request_id, declared, max_body_bytes)
        if not isinstance(body, bytes):
            return body  # 413 from the mid-read cap check on an undeclared body
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

        def fire() -> None:
            hook = getattr(resp, "on_sent", None)
            if callable(hook):
                try:
                    hook()
                except Exception:
                    _logger.error(f"on_sent hook failed:\n{traceback.format_exc()}")

        return _render(
            resp,
            request_id,
            accept=asgi.headers.get("accept-encoding", ""),
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
                writer.finish()  # done + wake: a parked consumer sees EOF without a poll
                hook = getattr(resp, "on_sent", None)
                if callable(hook):
                    try:
                        hook()
                    except Exception:
                        _logger.error(f"on_sent hook failed:\n{traceback.format_exc()}")

        async def generate():
            # One dedicated daemon thread per stream: the loop's default executor caps at
            # min(32, cpu+4) workers, which starved every stream past that width (the
            # baseline SSE 8-of-20 ceiling - plan/phase-7-optimization/evidence.md gap 1).
            # The generator polls the queue on the loop and parks on one event; producer
            # writes wake it thread-safely (part 2: no per-chunk to_thread round trip).
            writer.bind()
            producer = threading.Thread(target=produce, name="sse-producer", daemon=True)
            producer.start()
            try:
                while True:
                    chunk = await writer.read()
                    if chunk is _EOF:
                        break
                    yield chunk
            finally:
                writer.close()
                # Reap on a worker thread; producer exits once close() unblocks it.
                await anyio.to_thread.run_sync(producer.join)

        return StreamingResponse(generate(), status_code=resp.status, headers=headers)

    return app
