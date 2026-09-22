"""SSE live stream: ``GET /api/v1/stream``, the client registry, and the replay ring.

Implements docs/api-reference.md section 8: ``hello`` on connect, ``pulse`` every
``tick_s`` seconds from ``pulse_provider()``, ``trace``/``log``/``model`` as they are
published, and ``ping`` every ``_PING_S`` seconds. Frames are standard SSE - ``id:`` only
on trace events (so ``Last-Event-ID`` works per the spec), then ``event:``, ``data:``,
blank line. A reconnecting client with ``Last-Event-ID`` first receives the missed trace
events from the replay ring (newest ``replay`` kept, oldest evicted, oldest first), then
live events resume.

Concurrency: one condition variable guards the client list and the ring. Publishers
(``Writer.on_flush`` feeds ``publish_trace``) only touch memory and notify - they never
block on a socket. Each connected stream runs in its own handler thread, drains its queue
and writes outside the lock. Over ``max_clients`` the handler raises ``503
sse_unavailable`` before any headers are sent. ``mount`` registers the route with no
engine tracing (the middleware passes non-engine paths through untouched); auth is
Phase 3. The module-level ``hub`` singleton is the consumer-facing instance.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from collections import deque
from typing import TYPE_CHECKING, Any

from layawatch.http.types import HttpError, Request, Response
from layawatch.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from layawatch.http.router import Router

_logger = get_logger("http")

#: Seconds between ``ping`` events; a module constant so tests can shorten it.
_PING_S = 15.0


class _Client:
    """One connected stream: its event queue and cadence baselines (guarded by the hub lock)."""

    __slots__ = ("queue", "last_ping", "last_pulse")

    def __init__(self, now: float) -> None:
        self.queue: deque[tuple[str, dict, int | None]] = deque()
        self.last_pulse = now
        self.last_ping = now


class StreamHub:
    """Registry of live SSE clients, the trace replay ring, and the publish fan-out."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._clients: list[_Client] = []
        self._ring: deque[tuple[int, dict]] = deque(maxlen=200)
        self._next_id = 1
        self._stopping = False
        self._tick_s = 3.0
        self._ring_size = 1000
        self._max_clients = 16
        self._pulse_provider: Callable[[], dict] | None = None
        #: Same callable ``/api/v1/meta`` spreads at the top level; stored at attach time.
        self.counter_provider: Callable[[], dict] | None = None

    def attach(
        self,
        *,
        tick_s: float,
        ring_size: int,
        replay: int = 200,
        max_clients: int = 16,
        pulse_provider: Callable[[], dict],
        counter_provider: Callable[[], dict],
    ) -> None:
        """Configure the hub (once at startup): cadence, bounds, providers.

        Re-attaching reinitializes the hub for a fresh server: the replay ring is
        recreated at ``maxlen=replay`` (dropping old entries), event ids restart at 1,
        and any previous stop is cleared. Live clients keep their sockets.
        """
        with self._cond:
            self._tick_s = tick_s
            self._ring_size = ring_size
            self._max_clients = max_clients
            self._ring = deque(maxlen=replay)
            self._next_id = 1
            self._stopping = False
            self._pulse_provider = pulse_provider
            self.counter_provider = counter_provider

    def publish_trace(self, summary: dict) -> int:
        """Assign the next event id, record the summary for replay, fan out to clients.

        The summary payload is passed through untouched (api-reference section 5 shape);
        the id lives only in the ring tuple and the frame's ``id:`` line. Returns the
        assigned event id. This is the ``Writer.on_flush`` feed.
        """
        with self._cond:
            event_id = self._next_id
            self._next_id += 1
            self._ring.append((event_id, summary))
            for client in self._clients:
                client.queue.append(("trace", summary, event_id))
            self._cond.notify_all()
        return event_id

    def publish_log(self, entry: dict) -> None:
        """Fan a log entry (observability-model section 7 shape) out to connected clients."""
        self._publish("log", entry)

    def publish_model(self, payload: dict) -> None:
        """Fan a model change ``{loaded, action}`` out to connected clients."""
        self._publish("model", payload)

    def client_count(self) -> int:
        """Number of currently connected stream clients."""
        with self._cond:
            return len(self._clients)

    def stop(self) -> None:
        """Ask every connected stream to exit its loop (ordered shutdown)."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()

    def mount(self, router: Router) -> None:
        """Register ``GET /api/v1/stream`` on ``router`` (no engine trace)."""
        router.add("GET", "/api/v1/stream", self._handle)

    def _publish(self, name: str, payload: dict) -> None:
        with self._cond:
            for client in self._clients:
                client.queue.append((name, payload, None))
            self._cond.notify_all()

    def _handle(self, request: Request) -> Response:
        # EventSource cannot set headers on reconnect: accept the id as a query
        # parameter too, which is what web/src/lib/stream.ts sends.
        last_id = _parse_last_event_id(
            request.headers.get("last-event-id") or request.param("last_event_id")
        )
        with self._cond:
            if len(self._clients) >= self._max_clients:
                raise HttpError(
                    503,
                    "sse_unavailable",
                    f"stream client capacity reached ({self._max_clients})",
                )
            client = _Client(time.monotonic())
            self._clients.append(client)
            # Snapshot + registration in one critical section: a trace lands either in
            # this replay window (published before) or in the client's queue (after),
            # never both and never neither.
            replay = (
                [(event_id, summary) for event_id, summary in self._ring if event_id > last_id]
                if last_id is not None
                else []
            )

        def stream(writer: Any) -> None:
            try:
                _emit(
                    writer,
                    "hello",
                    {
                        "server_time": time.time(),
                        "tick": self._tick_s,
                        "ring_size": self._ring_size,
                    },
                )
                for event_id, summary in replay:
                    _emit(writer, "trace", summary, event_id)
                self._serve(client, writer)
            except OSError:
                pass  # BrokenPipe/ConnectionReset: the client hung up; the finally cleans up
            except Exception:
                _logger.error(f"sse stream failed: {traceback.format_exc()}")
            finally:
                with self._cond:
                    if client in self._clients:
                        self._clients.remove(client)
                    self._cond.notify_all()

        return Response(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
            stream=stream,
        )

    def _serve(self, client: _Client, writer: Any) -> None:
        """Write queued events, pulses and pings until the hub stops or the socket dies."""
        while True:
            with self._cond:
                while True:
                    if self._stopping:
                        return
                    now = time.monotonic()
                    pulse_due = now - client.last_pulse >= self._tick_s
                    ping_due = now - client.last_ping >= _PING_S
                    if client.queue or pulse_due or ping_due:
                        break
                    wake = min(client.last_pulse + self._tick_s, client.last_ping + _PING_S)
                    self._cond.wait(wake - now)
                pending = list(client.queue)
                client.queue.clear()
                now = time.monotonic()
                pulse_due = now - client.last_pulse >= self._tick_s
                ping_due = now - client.last_ping >= _PING_S
                if pulse_due:
                    client.last_pulse = now
                if ping_due:
                    client.last_ping = now
                provider = self._pulse_provider
            # Outside the lock: publishers must never block on sockets.
            for name, payload, event_id in pending:
                _emit(writer, name, payload, event_id)
            if pulse_due:
                _emit(writer, "pulse", provider() if provider is not None else {})
            if ping_due:
                _emit(writer, "ping", {})


def _parse_last_event_id(raw: str | None) -> int | None:
    """Parse the ``Last-Event-ID`` header; missing or unparseable means no replay."""
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _encode(name: str, payload: dict, event_id: int | None = None) -> bytes:
    """One SSE frame: optional ``id:``, ``event:``, single-line ``data:``, blank line."""
    data = json.dumps(payload, separators=(",", ":"))
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {name}\ndata: {data}\n\n".encode("utf-8")


def _emit(writer: Any, name: str, payload: dict, event_id: int | None = None) -> None:
    writer.write(_encode(name, payload, event_id))
    writer.flush()


#: Module singleton: consumers (writer on_flush, model changes, ``__main__``) publish here.
hub = StreamHub()
