"""SSE live stream: ``GET /api/v1/stream``, the client registry, and the replay ring.

Implements docs/api-reference.md section 8: ``hello`` on connect, ``pulse`` every
``tick_s`` seconds from ``pulse_provider()``, ``trace``/``log``/``model`` queued as they
are published and delivered with the next tick, and ``ping`` every ``_PING_S`` seconds. Frames are standard SSE - ``id:`` only
on trace events (so ``Last-Event-ID`` works per the spec), then ``event:``, ``data:``,
blank line. A reconnecting client with ``Last-Event-ID`` first receives the missed trace
events from the replay ring (newest ``replay`` kept, oldest evicted, oldest first), then
live events resume.

Catalog items 24 and 26 (docs/performance.md section 4.5): a stream loop does not wake
per publish - queued events coalesce into ONE write per tick carrying only the events
since that client's previous tick (diffs only), and the ``pulse`` payload is computed
once per tick window by the first client to cross its cadence (outside the lock) and
shared as pre-encoded bytes by every other client in the same window. That first claim
arms a midpoint refresh timer, so later windows find the cached frame already fresh and
crossings never queue behind a provider call (payload age at delivery becomes up to
half a tick plus provider time; evidence-4.5 wave-2.5). A write error
(dead or hung-up client) escapes on the first attempt - no retry - and the registry
``finally`` drops that client. Item 25 (idle-gated heartbeat) contradicts the pinned
test ``test_hello_first_with_exact_headers_then_pulse_and_ping``, so the documented
``_PING_S`` cadence is retained; see plan/phase-7-optimization/evidence-4.5.md.

Concurrency: one condition variable guards the client list, the ring and the shared
pulse frame. Publishers (``Writer.on_flush`` feeds ``publish_trace``) only touch memory -
they never block on a socket or behind a provider call. Each connected stream runs in its
own handler thread and writes outside the lock. Over ``max_clients`` (default 20: plan
task 7 runs ``bench.py sse`` with 20 clients and the closed catalog adds no capacity
knob) the handler raises ``503 sse_unavailable`` before any headers are sent. ``mount``
registers the route with no engine tracing (the middleware passes non-engine paths
through untouched); auth is Phase 3. The module-level ``hub`` singleton is the
consumer-facing instance.
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
        self._max_clients = 20
        self._pulse_provider: Callable[[], dict] | None = None
        #: Shared per-tick pulse frame (item 24): encoded once per tick window, reused by
        #: every client whose cadence fires while the frame is younger than ``tick_s``.
        self._pulse_frame: bytes | None = None
        self._pulse_frame_at = 0.0
        self._pulse_computing = False
        #: Single midpoint refresh timer (wave-2.5 part 1): at most one pending per hub.
        self._prefetch_timer: threading.Timer | None = None
        #: Same callable ``/api/v1/meta`` spreads at the top level; stored at attach time.
        self.counter_provider: Callable[[], dict] | None = None

    def attach(
        self,
        *,
        tick_s: float,
        ring_size: int,
        replay: int = 200,
        max_clients: int = 20,
        pulse_provider: Callable[(), dict],
        counter_provider: Callable[(), dict],
    ) -> None:
        """Configure the hub (once at startup): cadence, bounds, providers.

        ``max_clients`` defaults to 20 - the concurrent width plan task 7 measures with
        (``bench.py sse``); the closed catalog carries no config knob for it, so the
        default is the plan number. Re-attaching reinitializes the hub for a fresh
        server: the replay ring is recreated at ``maxlen=replay`` (dropping old
        entries), event ids restart at 1, the shared pulse frame cache is cleared, and
        any previous stop is cleared. Live clients keep their sockets.
        """
        with self._cond:
            self._tick_s = tick_s
            self._ring_size = ring_size
            self._max_clients = max_clients
            self._ring = deque(maxlen=replay)
            self._next_id = 1
            self._stopping = False
            self._pulse_provider = pulse_provider
            self._pulse_frame = None
            self._pulse_frame_at = 0.0
            self._pulse_computing = False
            if self._prefetch_timer is not None:
                self._prefetch_timer.cancel()
                self._prefetch_timer = None
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
            if self._prefetch_timer is not None:
                self._prefetch_timer.cancel()
                self._prefetch_timer = None
            self._cond.notify_all()

    def mount(self, router: Router) -> None:
        """Register ``GET /api/v1/stream`` on ``router`` (no engine trace)."""
        router.add("GET", "/api/v1/stream", self._handle)

    def _publish(self, name: str, payload: dict) -> None:
        with self._cond:
            for client in self._clients:
                client.queue.append((name, payload, None))

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
                head = [
                    _encode(
                        "hello",
                        {
                            "server_time": time.time(),
                            "tick": self._tick_s,
                            "ring_size": self._ring_size,
                        },
                    )
                ]
                head.extend(_encode("trace", summary, event_id) for event_id, summary in replay)
                writer.write(b"".join(head))  # one write: hello plus the whole replay window
                writer.flush()
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
        """Write one coalesced frame batch per tick until the hub stops or a write fails."""
        while True:
            batch = self._tick_batch(client)
            if batch is None:
                return
            # A dead client fails here on the first attempt and the error escapes to
            # _handle's finally, which drops it from the registry - no retry (item 26).
            writer.write(b"".join(batch))
            writer.flush()

    def _tick_batch(self, client: _Client) -> list[bytes] | None:
        """Collect this tick's frames under the lock; ``None`` once the hub is stopping.

        The wait loop only breaks on the client's own cadence, so publishes coalesce:
        everything queued since the previous tick leaves as one diff batch (item 24).
        The pulse payload is computed once per tick window - the first client whose
        cadence finds the cached frame older than ``tick_s`` claims it and runs the
        provider outside the lock; the others in that window wait for and reuse those
        exact pre-encoded bytes instead of re-running the provider.

        Wave-2.5 part 1 (convoy kill): every claim arms a midpoint refresh timer
        (``_arm_prefetch(tick_s / 2)``), so from the second window on, each crossing
        finds a frame younger than ``tick_s`` - no claim, no wait-path block, no
        compute/notify convoy on the shared condition. The claim/wait path remains only
        as the cold-start and client-gap fallback. The refresh moves payload age at
        delivery from provider-time to up to ``tick_s / 2`` + provider time; the
        documented cadence is one pulse per ``tick_s`` (evidence-4.5 wave-2.5).
        """
        compute = False
        with self._cond:
            while True:
                if self._stopping:
                    return None
                now = time.monotonic()
                pulse_due = now - client.last_pulse >= self._tick_s
                ping_due = now - client.last_ping >= _PING_S
                if pulse_due or ping_due:
                    break
                wake = min(client.last_pulse + self._tick_s, client.last_ping + _PING_S)
                self._cond.wait(wake - now)
            # Publishes do not wake this loop (they would defeat the coalescing); the
            # queue is drained whole when the cadence fires - waiters need no notify.
            pending = list(client.queue)
            client.queue.clear()
            now = time.monotonic()
            pulse_due = now - client.last_pulse >= self._tick_s
            ping_due = now - client.last_ping >= _PING_S
            if pulse_due:
                client.last_pulse = now
            if ping_due:
                client.last_ping = now
            frame: bytes | None = None
            if pulse_due:
                fresh = (
                    self._pulse_frame is not None
                    and now - self._pulse_frame_at < self._tick_s
                )
                if fresh:
                    frame = self._pulse_frame
                elif self._pulse_computing:
                    while self._pulse_computing and not self._stopping:
                        self._cond.wait()
                    if self._stopping:
                        return None
                    frame = self._pulse_frame
                else:
                    compute = True
                    self._pulse_computing = True  # claim: exactly one provider call per tick
        if compute:
            # Outside the lock: publishers must never block behind a provider call.
            frame = self._compute_pulse()
            self._arm_prefetch(self._tick_s / 2)  # next crossings find a fresh frame
        batch = [_encode(name, payload, event_id) for name, payload, event_id in pending]
        if frame is not None:
            batch.append(frame)
        if ping_due:
            batch.append(_encode("ping", {}))
        return batch

    def _compute_pulse(self) -> bytes:
        """Run the pulse provider, cache the encoded frame, release the claim.

        Called with ``_pulse_computing`` already claimed under the lock and no lock
        held: the provider runs outside it (publishers never wait on a provider call),
        then the shared frame is stored and any waiter on the old claim path wakes.
        """
        provider = self._pulse_provider
        try:
            payload = provider() if provider is not None else {}
            frame = _encode("pulse", payload)
        except Exception:
            _logger.error(f"pulse provider failed:\n{traceback.format_exc()}")
            frame = _encode("pulse", {})
        with self._cond:
            self._pulse_frame = frame
            self._pulse_frame_at = time.monotonic()
            self._pulse_computing = False
            self._cond.notify_all()
        return frame

    def _arm_prefetch(self, delay: float) -> None:
        """Schedule one midpoint frame refresh; the hub holds at most one pending timer.

        A claim arms ``tick_s / 2`` so the refresh lands half a window before the next
        crossing; each refresh re-arms ``tick_s``, keeping the refresh midway between
        crossings. Either way every crossing sees a frame younger than ``tick_s``.
        """
        with self._cond:
            if self._stopping or not self._clients or self._prefetch_timer is not None:
                return
            timer = threading.Timer(delay, self._prefetch)
            timer.daemon = True
            self._prefetch_timer = timer
            timer.start()

    def _prefetch(self) -> None:
        """Timer target: refresh the shared frame at the window midpoint, then re-arm.

        Only the hub's currently registered timer proceeds - attach, stop or a newer
        arm supersede a queued callback. With clients present and no claim in flight,
        the refresh computes the next frame so the crossings ahead never enter the
        claim/wait path; with no clients the pipeline simply stops until the next
        claim re-arms it.
        """
        with self._cond:
            if self._prefetch_timer is not threading.current_thread():
                return  # superseded while this callback was queued
            self._prefetch_timer = None
            if self._stopping or not self._clients or self._pulse_computing:
                return  # a claim in flight re-arms on completion; no clients, no demand
            self._pulse_computing = True
        self._compute_pulse()
        self._arm_prefetch(self._tick_s)


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


#: Module singleton: consumers (writer on_flush, model changes, ``__main__``) publish here.
hub = StreamHub()
