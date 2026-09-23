"""Wire contract on a real socket: gzip, streaming responses, meta, root negotiation, healthz.

Builds an in-process ``build_server`` (test_router/test_middleware pattern) on port 8097
with 8096 as fallback so parallel suites cannot collide, and asserts what a client
actually receives: compressed large JSON, length-less streaming responses with
``Connection: close``, the ``/api/v1/meta`` flat envelope with only allowlisted config,
``/`` content negotiation against a missing and a present ``web/out``, and the pinned
``/healthz`` bytes.
"""
from __future__ import annotations

import gzip
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

import layawatch
from layawatch.api.health import add_health_routes, add_meta_routes, add_root_route
from layawatch.config import Config
from layawatch.http.router import Router
from layawatch.http.server import build_server
from layawatch.http.types import Request, Response, json_response, text_response
from layawatch.log import ring_clear, ring_entries

# on_sent flags written by the stream handlers, read by the stream tests.
_SENT = {"stream": 0, "boom": 0}
_COUNTERS = {"obs_dropped_total": 7, "write_queue_depth": 3, "writes_total": 42}
_STARTED_AT = time.time() - 12.5
_BIG_PAYLOAD = {"items": [{"id": f"{i:04x}", "name": f"row-{i}", "score": i * 3} for i in range(80)]}
_SAFE_CONFIG = {
    "bind",
    "port",
    "device",
    "models",
    "english_only",
    "record",
    "api",
    "trust_proxy",
    "trace_sample",
    "capture_payloads",
    "payload_max",
    "retention_traces",
    "retention_days",
    "ring_traces",
    "stream_tick",
    "max_body_bytes",
    "socket_timeout",
    "log_level",
}
_HEALTHZ_BYTES = b'{"status":"ok","models":["english"],"device":"cpu","auth_enabled":false}'


class StubAdapter:
    """Duck-typed engine adapter stand-in for the /healthz payload."""

    def loaded(self) -> list[str]:
        return ["english"]

    def device(self) -> str:
        return "cpu"


def big_json(_request: Request) -> Response:
    return json_response(_BIG_PAYLOAD)


def small_json(_request: Request) -> Response:
    return json_response({"ok": True})


def html_page(_request: Request) -> Response:
    body = "<html><body>" + "x" * 2000 + "</body></html>"
    return text_response(body, content_type="text/html; charset=utf-8")


def stream_ok(_request: Request) -> Response:
    def write(wfile) -> None:
        wfile.write(b"alpha\n")
        wfile.write(b"beta\n")

    response = Response(stream=write)
    response.on_sent = lambda: _SENT.__setitem__("stream", _SENT["stream"] + 1)
    return response


def stream_boom(_request: Request) -> Response:
    def write(wfile) -> None:
        wfile.write(b"partial")
        raise RuntimeError("stream exploded")

    response = Response(stream=write)
    response.on_sent = lambda: _SENT.__setitem__("boom", _SENT["boom"] + 1)
    return response


def _pick_port() -> int:
    """Prefer 8097, fall back to 8096, then an ephemeral port if both are taken."""
    for port in (8097, 8096):
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            probe.close()
        return port
    return 0


def _serve(config: Config, router: Router):
    server = build_server(config, router)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return server, port, stop


def fetch(port: int, path: str, headers: dict[str, str] | None = None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.headers, response.read()


def _stream_error_logged() -> bool:
    return any(
        entry["source"] == "http"
        and entry["level"] == "error"
        and "stream exploded" in entry["message"]
        for entry in ring_entries()
    )


@pytest.fixture(scope="module")
def wire(tmp_path_factory) -> SimpleNamespace:
    base = tmp_path_factory.mktemp("wire")
    db_path = base / "state.sqlite3"
    db_path.write_bytes(b"x" * 4321)
    config = Config(
        bind="127.0.0.1",
        port=_pick_port(),
        state_dir=base,
        db_path=db_path,
        session_secret="sessiontok-should-not-leak",
    )
    router = Router()
    add_health_routes(router, StubAdapter())
    add_root_route(router, base / "web-out-absent")  # deliberately missing export
    add_meta_routes(
        router,
        config=config,
        started_at=_STARTED_AT,
        counters=lambda: dict(_COUNTERS),
        sse_clients=lambda: 0,
    )
    router.add("GET", "/big", big_json)
    router.add("GET", "/small", small_json)
    router.add("GET", "/page", html_page)
    router.add("GET", "/stream", stream_ok)
    router.add("GET", "/stream-boom", stream_boom)
    _server, port, stop = _serve(config, router)
    try:
        yield SimpleNamespace(port=port, base=base)
    finally:
        stop()


# -- gzip ---------------------------------------------------------------------


def test_large_json_is_gzipped_for_accepting_client(wire) -> None:
    status, headers, raw = fetch(wire.port, "/big", {"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers.get("Content-Encoding") == "gzip"
    assert headers.get("Vary") == "Accept-Encoding"
    assert int(headers["Content-Length"]) == len(raw)
    _plain_status, _plain_headers, plain = fetch(wire.port, "/big")
    assert gzip.decompress(raw) == plain
    assert json.loads(plain) == _BIG_PAYLOAD


def test_large_json_without_accept_encoding_header_is_plain(wire) -> None:
    status, headers, raw = fetch(wire.port, "/big")
    assert status == 200
    assert headers.get("Content-Encoding") is None
    assert headers.get("Vary") is None
    assert json.loads(raw) == _BIG_PAYLOAD
    assert int(headers["Content-Length"]) == len(raw)


def test_small_json_is_never_gzipped(wire) -> None:
    _status, headers, raw = fetch(wire.port, "/small", {"Accept-Encoding": "gzip"})
    assert headers.get("Content-Encoding") is None
    assert headers.get("Vary") is None
    assert raw == b'{"ok":true}'
    assert headers["Content-Length"] == "11"


def test_html_is_never_gzipped(wire) -> None:
    _status, headers, raw = fetch(wire.port, "/page", {"Accept-Encoding": "gzip"})
    assert len(raw) > 1024
    assert headers.get("Content-Type", "").startswith("text/html")
    assert headers.get("Content-Encoding") is None
    assert headers.get("Vary") is None


# -- streaming ---------------------------------------------------------------


def test_stream_response_has_no_length_and_closes(wire) -> None:
    before = _SENT["stream"]
    status, headers, body = fetch(wire.port, "/stream")
    assert status == 200
    assert headers.get("Content-Length") is None
    assert headers.get("Connection") == "close"
    assert body == b"alpha\nbeta\n"
    deadline = time.time() + 2
    while time.time() < deadline and _SENT["stream"] == before:
        time.sleep(0.01)  # the hook runs just after the client sees the last byte
    assert _SENT["stream"] == before + 1


def test_stream_failure_is_logged_and_connection_handling_survives(wire) -> None:
    ring_clear()
    before = _SENT["boom"]
    status, headers, body = fetch(wire.port, "/stream-boom")
    assert status == 200
    assert headers.get("Content-Length") is None
    assert headers.get("Connection") == "close"
    assert body == b"partial"
    deadline = time.time() + 2
    while time.time() < deadline and not (
        _SENT["boom"] == before + 1 and _stream_error_logged()
    ):
        time.sleep(0.01)
    assert _SENT["boom"] == before + 1  # on_sent fires despite the callback exception
    assert _stream_error_logged()
    # the exception stayed inside _send: the server keeps serving new connections
    _status, _headers, health = fetch(wire.port, "/healthz")
    assert json.loads(health)["status"] == "ok"


# -- meta ---------------------------------------------------------------------


def test_meta_reports_criterion_five_keys_counters_and_safe_config(wire) -> None:
    status, headers, raw = fetch(wire.port, "/api/v1/meta")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("application/json")
    text = raw.decode("utf-8")
    payload = json.loads(text)

    # plan criterion 5: all five keys with plausible values.
    for key in (
        "obs_dropped_total",
        "write_queue_depth",
        "db_size_bytes",
        "rss_mb",
        "sse_clients",
    ):
        assert key in payload
    assert payload["obs_dropped_total"] == 7
    assert payload["write_queue_depth"] == 3
    assert payload["writes_total"] == 42
    assert payload["db_size_bytes"] == 4321
    assert isinstance(payload["rss_mb"], (int, float)) and payload["rss_mb"] > 0
    assert payload["sse_clients"] == 0

    assert payload["version"] == layawatch.__version__
    assert isinstance(payload["version"], str)
    assert isinstance(payload["uptime_s"], (int, float)) and payload["uptime_s"] >= 12.4
    assert isinstance(payload["started_at"], float)
    assert payload["started_at"] == pytest.approx(_STARTED_AT)
    assert "deprecation" not in payload  # legacy shim counters removed in v0.1.0

    assert set(payload["config"]) == _SAFE_CONFIG
    for leaked in (
        "session_secret",
        "sessiontok",
        str(wire.base),
        "state_dir",
        "db_path",
        "web_root",
    ):
        assert leaked not in text


# -- root negotiation ---------------------------------------------------------


def test_root_answers_service_info_json_when_client_asks(wire) -> None:
    status, headers, raw = fetch(wire.port, "/", {"Accept": "application/json"})
    assert status == 200
    assert headers.get("Content-Type", "").startswith("application/json")
    payload = json.loads(raw)
    assert payload == {
        "service": "layawatch",
        "version": layawatch.__version__,
        "docs": "https://github.com/BLANCO-11/LayaWatch",
        "ui": "/",
    }


def test_root_browser_gets_build_page_when_export_is_missing(wire) -> None:
    browser = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    status, headers, raw = fetch(wire.port, "/", {"Accept": browser})
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/html")
    assert b"Console UI not built" in raw


def test_root_serves_index_html_when_export_exists(tmp_path: Path) -> None:
    web_root = tmp_path / "out"
    web_root.mkdir(parents=True)
    (web_root / "index.html").write_bytes(b"<h1>fixture home</h1>")
    config = Config(bind="127.0.0.1", port=_pick_port(), state_dir=tmp_path)
    router = Router()
    add_root_route(router, web_root)
    _server, port, stop = _serve(config, router)
    try:
        status, headers, body = fetch(port, "/", {"Accept": "text/html"})
        assert status == 200
        assert headers.get("Content-Type", "").startswith("text/html")
        assert body == b"<h1>fixture home</h1>"
        # explicit JSON negotiation still wins over the export
        _s, _h, raw = fetch(port, "/", {"Accept": "application/json"})
        assert json.loads(raw)["ui"] == "/"
    finally:
        stop()


# -- healthz ------------------------------------------------------------------


def test_healthz_bytes_are_pinned_and_untouched_by_gzip(wire) -> None:
    status, headers, raw = fetch(wire.port, "/healthz", {"Accept-Encoding": "gzip"})
    assert status == 200
    assert raw == _HEALTHZ_BYTES
    assert headers.get("Content-Encoding") is None
