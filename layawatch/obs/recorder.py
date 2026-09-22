"""Per-request trace recording: spans, sampling, ring, sinks (docs/architecture.md section 3).

One ``Context`` is bound to the creating thread via a thread-local; ``start`` installs it,
``finish`` clears it, and ``current_trace_id`` (also returned by ``Recorder.trace_provider``)
is what ``layawatch.log.set_trace_provider`` calls to attach a trace id to every log line.

Timing uses a single ``time.perf_counter()`` origin per trace, so every ``start_ms`` is an
offset from trace start and durations are comparable across spans. At ``finish`` the trace
records ``meta["framework_ms"] = duration_ms - sum(top-level span durations)`` clamped to
>= 0: the stated framework overhead from the observability model's totals rule.

Sampling keeps every error, refusal and playground run (status >= 400, an ``error_code``
set, or ``session_id``/``meta.source == "playground"``) unconditionally and applies
``sample_rate`` to the remaining successes with a seedable RNG. A sampled-out success is
still delivered to ``on_trace`` so rollups stay accurate, but without detail:
``meta["sampled"]`` is False, ``observations`` is empty and the trace never enters the
ring; its sample-out notice goes to ``on_log``. ``enabled=False`` returns an inert context
that does no timing, dict or sink work - the ``LAYWATCH_RECORD=0`` fast path for the
overhead A/B.
"""
from __future__ import annotations

import random
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from layawatch.obs.redact import capture_value
from layawatch.obs.vocabulary import observation_type, spec, validate

# Trace fields the recorder owns; Context.set() rejects them loudly instead of silently
# dropping a caller's value.
_READONLY_FIELDS = frozenset(
    {"id", "ts_start", "duration_ms", "status", "route", "method", "observations", "scores"}
)
_SETTABLE_FIELDS = frozenset(
    {
        "model",
        "route_reason",
        "lang",
        "queue_ms",
        "forward_ms",
        "state_bytes",
        "question_count",
        "client_key_id",
        "session_id",
        "error_code",
        "error_message",
        "tags",
        "meta",
    }
)


@dataclass
class Observation:
    """One timed or instantaneous unit of work; column-for-column the ``observations`` table."""

    id: str
    trace_id: str
    parent_id: str | None
    name: str
    type: str  # span | generation | event
    start_ms: float
    duration_ms: float
    status: str  # ok | error
    model: str | None
    input: str | None
    output: str | None
    meta: dict | None


@dataclass
class Score:
    """One judgement attached to a trace; column-for-column the ``scores`` table."""

    id: str
    trace_id: str
    name: str
    value: float
    data_type: str  # numeric | boolean | categorical
    source: str  # human | api | playground
    comment: str | None
    ts: float


@dataclass
class Trace:
    """One finished request; field names match the ``traces`` table column for column."""

    id: str
    ts_start: float
    duration_ms: float
    route: str
    method: str
    status: int
    model: str | None = None
    route_reason: str | None = None
    lang: str | None = None
    queue_ms: float = 0.0
    forward_ms: float | None = None
    state_bytes: int | None = None
    question_count: int | None = None
    client_key_id: str | None = None
    session_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    observations: list[Observation] = field(default_factory=list)
    scores: list[Score] = field(default_factory=list)


_local = threading.local()


def current_trace_id() -> str | None:
    """Active trace id on this thread, or ``None`` outside a recorded context."""
    ctx = getattr(_local, "ctx", None)
    return None if ctx is None else ctx.trace_id


def current_context() -> Context | None:
    """Active trace context on this thread, or ``None`` outside a recorded context.

    The HTTP middleware's ``ThreadLocalSpans`` proxy and the engine handlers resolve the
    per-request trace through this: the proxy is injected into the adapter once at
    construction, so it must be able to reach whichever context ``Recorder.start`` bound on
    the calling thread. With recording disabled ``start`` never touches the thread-local and
    this stays ``None`` - callers then record nothing instead of raising.
    """
    return getattr(_local, "ctx", None)


class _NullSpan:
    """Stateless no-op context manager reused by the disabled fast path (no per-span alloc)."""

    __slots__ = ()

    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


_NULL_SPAN = _NullSpan()


class _InertContext:
    """What ``Recorder.start`` returns when recording is disabled.

    Every method accepts the same arguments as the live ``Context`` but performs no timing,
    validation, capture or sink work; ``finish`` always returns ``None``.
    """

    __slots__ = ()

    trace_id: str | None = None
    client_ip: str = ""

    def span(self, name: str, input: Any = None, output: Any = None, **attrs: Any) -> _NullSpan:
        return _NULL_SPAN

    def event(
        self, name: str, input: Any = None, output: Any = None, **attrs: Any
    ) -> None:
        return None

    def set(self, **fields: Any) -> None:
        return None

    def set_meta(self, **entries: Any) -> None:
        return None

    def finish(self, status: int) -> None:
        return None


_INERT = _InertContext()


class _Span:
    """Context manager for one timed observation, created by ``Context.span``."""

    __slots__ = ("_ctx", "_name", "_type", "_meta", "_model", "_input", "_output", "_obs",
                 "_t_enter")

    def __init__(
        self,
        ctx: Context,
        name: str,
        entry_type: str,
        meta: dict,
        model: str | None,
        input_value: str | None,
        output_value: str | None,
    ) -> None:
        self._ctx = ctx
        self._name = name
        self._type = entry_type
        self._meta = meta
        self._model = model
        self._input = input_value
        self._output = output_value
        self._obs: Observation | None = None
        self._t_enter = 0.0

    def __enter__(self) -> _Span:
        now = time.perf_counter()
        ctx = self._ctx
        stack = ctx._stack
        obs = Observation(
            id=secrets.token_hex(8),
            trace_id=ctx.trace_id,
            parent_id=stack[-1].id if stack else None,
            name=self._name,
            type=self._type,
            start_ms=(now - ctx._t0) * 1000.0,
            duration_ms=0.0,
            status="ok",
            model=self._model,
            input=self._input,
            output=self._output,
            meta=self._meta,
        )
        ctx._observations.append(obs)
        stack.append(obs)
        self._obs = obs
        self._t_enter = now
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        obs = self._obs
        if obs is None:  # pragma: no cover - __exit__ without __enter__ cannot happen via `with`
            return False
        obs.duration_ms = (time.perf_counter() - self._t_enter) * 1000.0
        if exc_type is not None:
            obs.status = "error"
        stack = self._ctx._stack
        if stack and stack[-1] is obs:
            stack.pop()
        return False  # exceptions always re-raise


class Context:
    """One in-flight trace, bound to the thread that created it (see ``Recorder.start``)."""

    def __init__(
        self,
        recorder: Recorder,
        *,
        trace_id: str,
        route: str,
        method: str,
        client_ip: str,
        client_key_id: str | None,
        session_id: str | None,
    ) -> None:
        self.trace_id = trace_id
        self.client_ip = client_ip
        self._recorder = recorder
        self._t0 = time.perf_counter()
        self._ts_start = time.time()
        self._fields: dict = {
            "route": route,
            "method": method,
            "client_key_id": client_key_id,
            "session_id": session_id,
        }
        self._observations: list[Observation] = []
        self._stack: list[Observation] = []
        self._finished = False

    def span(self, name: str, input: Any = None, output: Any = None, **attrs: Any) -> _Span:
        """Open a timed span (or generation); the context manager marks it ``error`` on raise.

        ``input``/``output`` are capture-gated payloads: they become ``Observation.input``
        and ``Observation.output`` only when the recorder was built with ``capture=True``.
        Vocabulary errors (unknown name, unknown or mistyped attribute) raise at call time.
        """
        self._check_active()
        entry_type = observation_type(name)
        if entry_type == "event":
            raise ValueError(f"{name!r} is an event; record it with Context.event()")
        meta = validate(name, attrs)
        return _Span(
            self,
            name,
            entry_type,
            meta,
            meta.get("model"),
            self._capture(input),
            self._capture(output),
        )

    def event(self, name: str, input: Any = None, output: Any = None, **attrs: Any) -> Observation:
        """Record an instantaneous event observation (duration 0) in the current span."""
        self._check_active()
        entry = spec(name)
        if observation_type(name) != "event":
            raise ValueError(f"{name!r} is a timed span; record it with Context.span()")
        meta = validate(name, attrs)
        now = time.perf_counter()
        obs = Observation(
            id=secrets.token_hex(8),
            trace_id=self.trace_id,
            parent_id=self._stack[-1].id if self._stack else None,
            name=name,
            type="event",
            start_ms=(now - self._t0) * 1000.0,
            duration_ms=0.0,
            status="ok" if entry.status else "error",
            model=meta.get("model"),
            input=self._capture(input),
            output=self._capture(output),
            meta=meta,
        )
        self._observations.append(obs)
        return obs

    def set(self, **fields: Any) -> None:
        """Set trace-level fields (model, lang, route_reason, queue_ms, ...).

        Recorder-owned fields (``id``, ``status``, ``route``, ...) and unknown names raise
        ``ValueError``. ``error_message`` is truncated to 500 characters per the docs
        trace-attribute table.
        """
        self._check_active()
        for key, value in fields.items():
            if key in _READONLY_FIELDS:
                raise ValueError(f"trace field {key!r} is owned by the recorder; not settable")
            if key not in _SETTABLE_FIELDS:
                raise ValueError(f"unknown trace field {key!r}")
            if key == "error_message" and value is not None:
                value = str(value)[:500]
            self._fields[key] = value

    def set_meta(self, **entries: Any) -> None:
        """Merge ``entries`` into the trace ``meta`` dict (``set(meta=...)`` replaces it).

        Meta has several independent producers - auth arming from the middleware, capture and
        schema flags from ``finish``, playground source from the run handler - so each writes
        its own keys through this helper instead of clobbering the others.
        """
        self._check_active()
        meta = dict(self._fields.get("meta") or {})
        meta.update(entries)
        self._fields["meta"] = meta

    def finish(self, status: int) -> Trace | None:
        """Close the trace: assemble totals, sample, ring, ``on_trace``.

        Returns the assembled ``Trace`` for an enabled recorder; ``None`` only on a double
        close (and for the inert context). Kept traces enter the ring. Sampled-out
        successes are still delivered to ``on_trace`` for rollup accuracy, without detail:
        ``meta["sampled"]`` is False, ``observations`` is empty and no ring entry is made.
        Errors, refusals and playground runs are never sampled out.
        """
        if self._finished:
            return None
        self._finished = True
        if getattr(_local, "ctx", None) is self:
            _local.ctx = None
        duration = max(0.0, (time.perf_counter() - self._t0) * 1000.0)
        for obs in self._stack:  # spans leaked without an exit still close consistently
            obs.duration_ms = max(0.0, duration - obs.start_ms)
        self._stack.clear()
        # One draw per trace keeps the seeded decision sequence independent of the error mix.
        draw = self._recorder._rng.random()
        caller_meta = self._fields.get("meta") or {}
        always_keep = (
            status >= 400
            or bool(self._fields.get("error_code"))
            or bool(self._fields.get("session_id"))
            or caller_meta.get("source") == "playground"
        )
        keep = always_keep or draw < self._recorder.sample_rate
        top_level = 0.0
        for obs in self._observations:
            if obs.parent_id is None:
                top_level += obs.duration_ms
        framework = duration - top_level
        if framework < 0.0:  # overlapping top-level spans can only overshoot the sum
            framework = 0.0
        meta = dict(caller_meta)
        meta["framework_ms"] = framework
        meta["payload_capture"] = self._recorder.capture
        meta["sampled"] = keep
        trace = Trace(
            id=self.trace_id,
            ts_start=self._ts_start,
            duration_ms=duration,
            route=self._fields["route"],
            method=self._fields["method"],
            status=status,
            model=self._fields.get("model"),
            route_reason=self._fields.get("route_reason"),
            lang=self._fields.get("lang"),
            queue_ms=self._fields.get("queue_ms", 0.0),
            forward_ms=self._fields.get("forward_ms"),
            state_bytes=self._fields.get("state_bytes"),
            question_count=self._fields.get("question_count"),
            client_key_id=self._fields.get("client_key_id"),
            session_id=self._fields.get("session_id"),
            error_code=self._fields.get("error_code"),
            error_message=self._fields.get("error_message"),
            tags=list(self._fields.get("tags") or ()),
            meta=meta,
            observations=list(self._observations) if keep else [],
            scores=[],
        )
        recorder = self._recorder
        if keep:
            with recorder._ring_lock:
                recorder._ring.append(trace)
        else:
            self._notify_dropped(status)
        sink = recorder._on_trace
        if sink is not None:
            try:
                sink(trace)
            except Exception:
                pass  # sinks must never take the request down (log.py contract)
        return trace

    def _capture(self, value: Any) -> str | None:
        if value is None:
            return None
        recorder = self._recorder
        return capture_value(value, enabled=recorder.capture, max_bytes=recorder.payload_max)

    def _notify_dropped(self, status: int) -> None:
        """Report a sampled-out trace through ``on_log`` so drops stay visible in the Logs view."""
        sink = self._recorder._on_log
        if sink is None:
            return
        entry = {
            "ts": time.time(),
            "level": "debug",
            "source": "server",
            "trace_id": self.trace_id,
            "message": f"trace {self.trace_id} sampled out (status {status})",
        }
        try:
            sink(entry)
        except Exception:
            pass  # same contract as log.py sinks: a failure here must never take down the caller

    def _check_active(self) -> None:
        if self._finished:
            raise RuntimeError(f"trace {self.trace_id} already finished")


class Recorder:
    """Owns the trace ring, the sampling policy and the sinks; creates per-request contexts."""

    def __init__(
        self,
        ring_size: int = 1000,
        sample_rate: float = 1.0,
        enabled: bool = True,
        capture: bool = False,
        payload_max: int = 2048,
        on_trace: Callable[[Trace], None] | None = None,
        on_log: Callable[[dict], None] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if ring_size < 1:
            raise ValueError("ring_size must be >= 1")
        if not 0.0 <= sample_rate <= 1.0:
            raise ValueError("sample_rate must be within [0.0, 1.0]")
        if payload_max < 0:
            raise ValueError("payload_max must be >= 0")
        self.ring_size = ring_size
        self.sample_rate = sample_rate
        self.enabled = enabled
        self.capture = capture
        self.payload_max = payload_max
        self._on_trace = on_trace
        self._on_log = on_log
        self._rng = rng if rng is not None else random.Random()
        self._ring: deque[Trace] = deque(maxlen=ring_size)
        self._ring_lock = threading.Lock()

    def start(
        self,
        route: str,
        method: str,
        request_id: str | None = None,
        client_ip: str = "",
        client_key_id: str | None = None,
        session_id: str | None = None,
    ) -> Context | _InertContext:
        """Bind a new trace context to this thread and return it.

        ``request_id`` becomes the trace id (middleware generates the ``X-Request-Id``);
        when omitted the recorder generates an 8-hex id. With ``enabled=False`` an inert
        context is returned and no thread-local is touched.
        """
        if not self.enabled:
            return _INERT
        ctx = Context(
            self,
            trace_id=request_id or secrets.token_hex(4),
            route=route,
            method=method,
            client_ip=client_ip,
            client_key_id=client_key_id,
            session_id=session_id,
        )
        _local.ctx = ctx
        return ctx

    def ring_traces(self, n: int | None = None) -> list[Trace]:
        """Ring snapshot oldest to newest; ``n`` keeps the newest entries (like log ring_entries)."""
        with self._ring_lock:
            items = list(self._ring)
        if n is not None and n >= 0:
            items = items[-n:] if n else []
        return items

    def trace_provider(self) -> Callable[[], str | None]:
        """Zero-arg provider for ``layawatch.log.set_trace_provider``."""
        return current_trace_id
