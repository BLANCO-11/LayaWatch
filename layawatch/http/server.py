"""Serving paths for the FastAPI app built by ``layawatch.app``.

Production is ``run()``: uvicorn serves a ready-made FastAPI app on
``config.bind:config.port`` with ``Server: LayaWatch`` and logs the start/stop
lines. The HTTP wire behavior (request limits, header normalization, gzip,
SSE framing) lives in the app bridge; this module only moves bytes.

``build_server()`` is a TEST FIXTURE ONLY - not a serving path. It drives the
same ``create_app(dispatch)`` bridge through a threading HTTP server over raw
sockets - close-delimited bodies and an explicit ``Connection: close`` on
every response - so suites can assert exact wire bytes in-process without
spawning uvicorn. No production path calls it; the deferred "re-fit test
suite to FastAPI" work owns its eventual fate.
"""
from __future__ import annotations

import asyncio
import socket
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import uvicorn

from layawatch.app import create_app
from layawatch.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from fastapi import FastAPI

    from layawatch.config import Config
    from layawatch.http.router import Router

_logger = get_logger("http")


def _listening_socket(host: str, port: int) -> socket.socket:
    """Bind the listening socket for ``run`` with the 4.1 item 1 flags set.

    ``TCP_NODELAY`` on the listening socket is inherited by every socket ``accept()``
    yields (verified on this kernel: listener 1 -> accepted 1), so each connection in
    the UI's keep-alive burst writes without Nagle delay. ``SO_REUSEADDR`` plus
    backlog 2048 match uvicorn's own ``bind_socket`` defaults.
    """
    family, _type, _proto, _canon, sockaddr = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    sock = socket.socket(family, _type, _proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.bind(sockaddr)
    sock.listen(2048)
    return sock


def run(config: Config, app: FastAPI) -> None:
    """Serve ``app`` on ``config.bind:config.port`` until SIGTERM/SIGINT.

    4.1 item 1: keep-alive is pinned explicitly (``timeout_keep_alive=5`` is also
    uvicorn's default - HTTP/1.1 connections persist for the UI's asset burst) and the
    listening socket carries ``TCP_NODELAY`` (``_listening_socket``); uvicorn itself
    sets neither anywhere.
    """
    _logger.info(f"serving on {config.bind}:{config.port}")
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.bind,
            port=config.port,
            headers=[("server", "LayaWatch")],
            access_log=False,
            timeout_keep_alive=5,
        )
    )
    sock = _listening_socket(config.bind, config.port)
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()  # shutdown() already closed it; this covers a failed startup
        _logger.info("server stopped")


class _BridgeHandler(BaseHTTPRequestHandler):
    """One raw HTTP/1.1 exchange driving ``create_app``'s catch-all ASGI route."""

    server_version = "LayaWatch"
    sys_version = ""  # no Python banner on the wire

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, fmt: str, *args: object) -> None:
        _logger.debug(f"build_server {fmt % args}")

    # The parser dispatches on the verb; every method takes the same path.
    def do_GET(self) -> None:
        self._exchange()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_GET

    def _exchange(self) -> None:
        bridge = self.server  # _BridgeServer; BaseServer is untyped at runtime
        started = False
        declared: int | None = None
        raw_length = self.headers.get("Content-Length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                declared = None  # kept in the headers for the bridge to answer 400
        # Oversized bodies are refused on the declared length alone (the bridge
        # answers 413 before it ever asks for the bytes), so never read them here.
        body = b""
        if declared is not None and 0 < declared <= bridge.body_cap:
            body = self.rfile.read(declared)
        path, _, query = self.path.partition("?")
        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": self.command,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("latin-1"),
            "query_string": query.encode("latin-1"),
            "root_path": "",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in self.headers.items()
            ],
            "client": (self.client_address[0], self.client_address[1]),
            "server": (bridge.server_address[0], bridge.server_address[1]),
            "state": {},
        }
        pending: list[dict] = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive() -> dict:
            if pending:
                return pending.pop(0)
            # Body consumed; this fixture detects client hang-ups through write
            # errors (the next SSE tick fails) rather than a pending read - a
            # parked reader would pin an executor thread until EOF and deadlock
            # the shutdown that sends the EOF. Cancellation at the end of the
            # exchange is the disconnect signal.
            await asyncio.Future()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                self.send_response(message["status"])
                for name, value in message.get("headers", ()):
                    self.send_header(name.decode("latin-1"), value.decode("latin-1"))
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
            elif message["type"] == "http.response.body":
                chunk = message.get("body") or b""
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()

        try:
            asyncio.run(bridge.app(scope, receive, send))
        except OSError:
            self.close_connection = True  # client hung up mid-exchange
        except Exception:
            _logger.error(f"build_server request failed:\n{traceback.format_exc()}")
            self.close_connection = True
            if not started:
                try:
                    payload = b'{"error":{"code":"internal_error"}}'
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass


class _BridgeServer(ThreadingHTTPServer):
    """Thread-per-connection server carrying the ASGI app and the body cap."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: FastAPI, body_cap: int) -> None:
        super().__init__(address, _BridgeHandler)
        self.app = app
        self.body_cap = body_cap


def build_server(
    config: Config,
    router: Router,
    *,
    dispatch: Callable[..., Any] | None = None,
) -> ThreadingHTTPServer:
    """Serve ``create_app(dispatch)`` on ``config.bind:config.port`` for the test suites.

    Test fixture only (see the module docstring): ``dispatch`` defaults to
    ``router.dispatch``; suites with their own instrumented handle pass it
    explicitly. Drive it with ``serve_forever`` on a thread and tear it down with
    ``shutdown`` plus ``server_close``. Binding an in-use port raises ``OSError`` -
    the caller's retry loop depends on it.
    """
    app = create_app(
        dispatch if dispatch is not None else router.dispatch,
        max_body_bytes=config.max_body_bytes,
    )
    return _BridgeServer((config.bind, config.port), app, config.max_body_bytes)
