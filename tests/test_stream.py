"""SSE stream contract: framing, cadence, publish fan-out, Last-Event-ID replay, capacity.

Every test drives a real ``build_server`` on 127.0.0.1:8097 with raw sockets, so the bytes
on the wire - not just the hub internals - are what is asserted. The module singleton
``stream.hub`` is re-attached per test (fresh ring, ids restarting at 1); ``_PING_S`` is
monkeypatched where the 15 s heartbeat would otherwise dominate the runtime.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager

from layawatch.api import stream
from layawatch.config import Config
from layawatch.http.router import Router
from layawatch.http.server import build_server

PORT = 8097
_HOST = ("127.0.0.1", PORT)
_IO_TIMEOUT = 3.0

_PULSE = {
    "requests_per_s": 4.0,
    "errors_5m": 1,
    "p50": 412.5,
    "p95": 900.0,
    "queue_ms": 5.2,
    "rss_mb": 120.0,
    "dropped_total": 0,
    "queue_depth": 0,
}


class _Conn:
    """Raw HTTP + SSE reader over one socket: response headers, then event blocks."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.status = 0
        self.headers: dict[str, str] = {}
        self.last_raw = b""
        self._buf = b""
        while b"\r\n\r\n" not in self._buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise AssertionError("server closed before sending response headers")
            self._buf += chunk
        head, _, self._buf = self._buf.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        self.status = int(lines[0].split()[1])
        for line in lines[1:]:
            name, _, value = line.partition(b":")
            self.headers[name.decode("ascii").lower()] = value.strip().decode("ascii")

    def read_event(self, timeout: float = _IO_TIMEOUT) -> dict:
        """Return the next event as ``{"id", "event", "data"}``; stores its raw bytes."""
        deadline = time.monotonic() + timeout
        while True:
            idx = self._buf.find(b"\n\n")
            if idx >= 0:
                self.last_raw = self._buf[: idx + 2]
                self._buf = self._buf[idx + 2 :]
                return _parse_event(self.last_raw[:-2])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no SSE event within {timeout}s (buf={self._buf!r})")
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout as exc:
                raise AssertionError(f"no SSE event within {timeout}s") from exc
            if not chunk:
                raise AssertionError(f"stream closed while waiting for an event ({self._buf!r})")
            self._buf += chunk

    def read_json_body(self, timeout: float = _IO_TIMEOUT) -> dict:
        """Read the full body of a non-streaming response (Content-Length framed)."""
        length = int(self.headers.get("content-length", "0"))
        deadline = time.monotonic() + timeout
        while len(self._buf) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("timed out reading the response body")
            self.sock.settimeout(remaining)
            self._buf += self.sock.recv(4096)
        return json.loads(self._buf[:length])

    def close(self) -> None:
        self.sock.close()


class _Harness:
    """Owns the server thread and every socket it opened; teardown closes them all."""

    def __init__(self, hub: stream.StreamHub) -> None:
        self.hub = hub
        self._conns: list[_Conn] = []

    def open(self, last_event_id: int | None = None) -> _Conn:
        sock = socket.create_connection(_HOST, timeout=5)
        try:
            lines = [
                "GET /api/v1/stream HTTP/1.1",
                "Host: 127.0.0.1",
                "Accept: text/event-stream",
            ]
            if last_event_id is not None:
                lines.append(f"Last-Event-ID: {last_event_id}")
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
            conn = _Conn(sock)
        except BaseException:
            sock.close()
            raise
        self._conns.append(conn)
        return conn

    def close_all(self) -> None:
        for conn in self._conns:
            conn.close()
        self._conns.clear()


def _parse_event(block: bytes) -> dict:
    event_id = None
    name = "message"
    data = b""
    for line in block.split(b"\n"):
        if line.startswith(b"id: "):
            event_id = int(line[4:])
        elif line.startswith(b"event: "):
            name = line[7:].decode("ascii")
        elif line.startswith(b"data: "):
            data = line[6:]
    payload = json.loads(data) if data else None
    return {"id": event_id, "event": name, "data": payload}


def _read_until(conn: _Conn, name: str, timeout: float = _IO_TIMEOUT) -> dict:
    """Read events until the one named ``name`` arrives (pulse/ping may interleave)."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"no {name!r} event within {timeout}s")
        event = conn.read_event(remaining)
        if event["event"] == name:
            return event


def _wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s")


@contextmanager
def server(tmp_path, *, tick_s=0.2, ring_size=1000, replay=200, max_clients=16, pulse=_PULSE):
    """Attach the singleton hub, mount it on a fresh router, serve on 127.0.0.1:8097."""
    if stream.hub.client_count() != 0:
        raise AssertionError("leaked stream client from an earlier test")
    stream.hub.attach(
        tick_s=tick_s,
        ring_size=ring_size,
        replay=replay,
        max_clients=max_clients,
        pulse_provider=lambda: dict(pulse),
        counter_provider=lambda: {"obs_dropped_total": 0},
    )
    router = Router()
    stream.hub.mount(router)
    config = Config.load({"LAYA_STATE_DIR": str(tmp_path), "LAYA_PORT": str(PORT)})
    # A sibling test run may hold 8097 for a moment; brief retry, then fail loudly.
    deadline = time.monotonic() + 3
    while True:
        try:
            httpd = build_server(config, router)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    harness = _Harness(stream.hub)
    try:
        yield harness
    finally:
        harness.close_all()
        wait_deadline = time.monotonic() + 3
        while stream.hub.client_count() and time.monotonic() < wait_deadline:
            time.sleep(0.02)  # closed sockets make each stream fail its next write and exit
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_hello_first_with_exact_headers_then_pulse_and_ping(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stream, "_PING_S", 0.3)
    pulse = dict(_PULSE)
    with server(tmp_path, pulse=pulse) as harness:
        conn = harness.open()
        assert conn.status == 200
        assert conn.headers["content-type"] == "text/event-stream"
        assert conn.headers["cache-control"] == "no-cache"
        assert conn.headers["x-accel-buffering"] == "no"
        assert conn.headers["connection"] == "close"
        assert "content-length" not in conn.headers

        hello = conn.read_event()
        assert hello["event"] == "hello"
        assert hello["id"] is None
        assert set(hello["data"]) == {"server_time", "tick", "ring_size"}
        assert hello["data"]["tick"] == 0.2
        assert hello["data"]["ring_size"] == 1000
        assert abs(hello["data"]["server_time"] - time.time()) < 5
        expected_hello = (
            "event: hello\ndata: " + json.dumps(hello["data"], separators=(",", ":")) + "\n\n"
        ).encode("utf-8")
        assert conn.last_raw == expected_hello

        got_pulse = conn.read_event()
        assert got_pulse["event"] == "pulse"
        assert got_pulse["data"] == pulse
        expected_pulse = (
            "event: pulse\ndata: " + json.dumps(pulse, separators=(",", ":")) + "\n\n"
        ).encode("utf-8")
        assert conn.last_raw == expected_pulse

        got_ping = conn.read_event()
        assert got_ping["event"] == "ping"
        assert got_ping["data"] == {}
        assert conn.last_raw == b"event: ping\ndata: {}\n\n"


def test_publish_trace_log_model_fan_out_with_exact_framing(tmp_path) -> None:
    with server(tmp_path) as harness:
        conn = harness.open()
        assert conn.read_event()["event"] == "hello"

        summary = {
            "id": "9f2c1a4b",
            "ts_start": 1790080267.284,
            "duration_ms": 412.5,
            "route": "/predict",
            "method": "POST",
            "status": 200,
            "model": "english",
            "route_reason": "state is English",
            "queue_ms": 5.2,
            "forward_ms": 391.4,
            "client_key_id": "k_3f8a",
            "span_count": 9,
            "error_code": None,
        }
        event_id = harness.hub.publish_trace(summary)
        trace = _read_until(conn, "trace")
        assert trace["id"] == event_id
        assert trace["data"] == summary  # same keys, same values: nothing added
        expected_trace = (
            f"id: {event_id}\nevent: trace\ndata: "
            f"{json.dumps(summary, separators=(',', ':'))}\n\n"
        ).encode("utf-8")
        assert conn.last_raw == expected_trace

        entry = {
            "ts": 1790080267.284,
            "level": "info",
            "source": "server",
            "trace_id": "9f2c1a4b",
            "message": "predict request_id=9f2c1a4b status=200 ms=412.5",
        }
        harness.hub.publish_log(entry)
        log = _read_until(conn, "log")
        assert log["id"] is None
        assert log["data"] == entry

        payload = {"loaded": ["english"], "action": "unload"}
        harness.hub.publish_model(payload)
        model = _read_until(conn, "model")
        assert model["id"] is None
        assert model["data"] == payload


def test_last_event_id_replays_missed_traces_and_fresh_connect_gets_none(tmp_path) -> None:
    with server(tmp_path, replay=5) as harness:
        ids = [harness.hub.publish_trace({"id": f"{i:08x}", "status": 200}) for i in range(10)]

        # Fresh connect (no header): zero replayed traces - the next event is live pulse.
        fresh = harness.open()
        assert fresh.read_event()["event"] == "hello"
        assert fresh.read_event(timeout=2)["event"] == "pulse"
        fresh.close()

        # Reconnect after id 4: ten published with replay=5 means the ring kept only the
        # newest 5 (oldest evicted); they come back oldest first with their id lines.
        resumed = harness.open(last_event_id=ids[4])
        assert resumed.read_event()["event"] == "hello"
        replayed = [resumed.read_event() for _ in range(5)]
        assert [event["event"] for event in replayed] == ["trace"] * 5
        assert [event["id"] for event in replayed] == ids[5:]
        assert [event["data"]["id"] for event in replayed] == [f"{i:08x}" for i in range(5, 10)]


def test_capacity_rejects_with_503_and_releases_after_close(tmp_path) -> None:
    with server(tmp_path, max_clients=1) as harness:
        first = harness.open()
        assert first.read_event()["event"] == "hello"
        assert harness.hub.client_count() == 1

        second = harness.open()
        assert second.status == 503
        error = second.read_json_body()["error"]
        assert error["code"] == "sse_unavailable"
        second.close()
        assert harness.hub.client_count() == 1  # the rejected request never registered

        first.close()
        _wait_for(lambda: harness.hub.client_count() == 0)

        third = harness.open()
        assert third.read_event()["event"] == "hello"
        assert harness.hub.client_count() == 1


def test_disconnect_cleans_up_and_later_publishes_do_not_raise(tmp_path) -> None:
    with server(tmp_path) as harness:
        conn = harness.open()
        assert conn.read_event()["event"] == "hello"
        assert harness.hub.client_count() == 1

        conn.close()  # drop mid-stream with no protocol goodbye
        _wait_for(lambda: harness.hub.client_count() == 0)

        harness.hub.publish_trace({"id": "deadbeef", "status": 500})
        harness.hub.publish_log(
            {"ts": 1.0, "level": "warn", "source": "server", "trace_id": None, "message": "gone"}
        )
        harness.hub.publish_model({"loaded": [], "action": "unload"})
        assert harness.hub.client_count() == 0
