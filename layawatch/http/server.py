"""ThreadingHTTPServer wiring: request limits, header normalization, graceful shutdown.

``build_server`` binds ``config.bind:config.port``. ``run`` serves until SIGTERM/SIGINT and then
shuts down from a daemon thread (``shutdown()`` must not be called on the serving thread), logging
start and stop lines through ``layawatch.log``. Per request: header keys are lowercased, an
inbound ``X-Request-Id`` is honored only when it matches ``^[0-9a-f]{8}$`` (otherwise
``secrets.token_hex(4)``), a declared body larger than ``max_body_bytes`` answers
``413 payload_too_large`` without reading the body and closes the connection, and
``POST``/``PATCH``/``PUT`` with no ``Content-Length`` answers ``411 length_required``. Responses
are HTTP/1.1 with ``Content-Length`` on every one; the default access log is downgraded to debug
level through ``layawatch.log``. No gzip, timing, auth, or body parsing yet (later phases).
"""
from __future__ import annotations

import re
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import FrameType
from typing import TYPE_CHECKING, Any

from layawatch.http.types import HttpError, Request, Response, error_response
from layawatch.log import get_logger

if TYPE_CHECKING:
    from layawatch.config import Config
    from layawatch.http.router import Router

_logger = get_logger("http")
_REQUEST_ID = re.compile(r"^[0-9a-f]{8}$")
_BODY_REQUIRED = frozenset({"POST", "PATCH", "PUT"})


class _AppServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the bound config and router between handler and factory."""

    config: Config
    router: Router


class _Handler(BaseHTTPRequestHandler):
    """Serves one connection: enforce limits, build a Request, dispatch, write the Response."""

    protocol_version = "HTTP/1.1"
    server_version = "LayaWatch"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.server.config.socket_timeout)

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def log_message(self, format: str, *args: Any) -> None:
        _logger.debug(format % args)

    def _handle(self) -> None:
        config = self.server.config
        raw_id = self.headers.get("X-Request-Id")
        request_id = raw_id if raw_id and _REQUEST_ID.match(raw_id) else secrets.token_hex(4)
        headers = {name.lower(): value for name, value in self.headers.items()}

        declared = self.headers.get("Content-Length")
        if declared is None:
            if self.command in _BODY_REQUIRED:
                self._send(
                    _error(411, "length_required", "Content-Length header is required", request_id),
                    close=True,
                )
                return
            body = b""
        else:
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length < 0:
                self._send(
                    _error(
                        400, "invalid_content_length", "invalid Content-Length header", request_id
                    ),
                    close=True,
                )
                return
            if length > config.max_body_bytes:
                self._send(
                    _error(
                        413,
                        "payload_too_large",
                        f"request body exceeds {config.max_body_bytes} bytes",
                        request_id,
                    ),
                    close=True,
                )
                return
            body = self.rfile.read(length)

        request = Request.build(
            self.command, self.path, headers, body, request_id, self.client_address[0]
        )
        self._send(self.server.router.dispatch(request))

    def _send(self, response: Response, *, close: bool = False) -> None:
        if close:
            self.close_connection = True
            response.headers["Connection"] = "close"
        if response.status in (204, 304):
            for name in [n for n in response.headers if n.lower() == "content-length"]:
                del response.headers[name]
        elif not any(name.lower() == "content-length" for name in response.headers):
            response.headers["Content-Length"] = str(len(response.body))
        try:
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def _error(status: int, code: str, message: str, request_id: str) -> Response:
    """Envelope for server-generated errors that bypass the router."""
    return error_response(HttpError(status, code, message), request_id)


def build_server(config: Config, router: Router) -> ThreadingHTTPServer:
    """Bind a threading HTTP server to ``config.bind:config.port`` wired to ``router``."""
    server = _AppServer((config.bind, config.port), _Handler)
    server.config = config
    server.router = router
    return server


def run(config: Config, router: Router) -> None:
    """Serve until SIGTERM/SIGINT, then shut down gracefully; logs start and stop lines."""
    server = build_server(config, router)

    def terminate(_signum: int, _frame: FrameType | None) -> None:
        threading.Thread(target=server.shutdown, name="laya-shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    _logger.info(f"serving on {config.bind}:{config.port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        _logger.info("server stopped")