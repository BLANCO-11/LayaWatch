"""Level-filtered logging with a thread-safe in-memory ring sink.

Stderr lines look like ``YYYY-MM-DD HH:MM:SS source message``. The ring (fixed size, default 2000)
feeds the Logs view and the SSE ``log`` events. Phase 1 registers two optional hooks:

- ``set_sink(fn)``: every entry is offered to the storage writer (which must enqueue, not block).
- ``set_trace_provider(fn)``: attaches ``trace_id`` from the active recorder context.

Hooks must never take the caller down: sink and provider exceptions are swallowed (the writer logs
its own failures).
"""
from __future__ import annotations

import sys
import threading
import time
from collections import deque
from typing import Callable

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_MIN = 10

_config_lock = threading.Lock()
_ring_lock = threading.Lock()
_ring: deque[dict] = deque(maxlen=2000)

_sink: Callable[[dict], None] | None = None
_trace_provider: Callable[[], str | None] | None = None


def configure(level: str = "info", ring_size: int = 2000) -> None:
    """Set the minimum level and resize the ring. Raises ValueError on a bad level or size."""
    global _MIN, _ring
    if level not in _LEVELS:
        raise ValueError(f"unknown log level {level!r}; expected one of {sorted(_LEVELS)}")
    if ring_size < 100:
        raise ValueError("log ring_size must be at least 100")
    with _ring_lock:
        new_ring: deque[dict] = deque(maxlen=ring_size)
        new_ring.extend(_ring)
        _ring = new_ring
    with _config_lock:
        _MIN = _LEVELS[level]


def set_sink(sink: Callable[[dict], None] | None) -> None:
    """Register the Phase 1 writer as the log sink (None removes it)."""
    global _sink
    _sink = sink


def set_trace_provider(provider: Callable[[], str | None] | None) -> None:
    """Register the provider that returns the active trace id (None removes it)."""
    global _trace_provider
    _trace_provider = provider


class Logger:
    """Writes to stderr when the level allows it; always records into the ring."""

    __slots__ = ("_source",)

    def __init__(self, source: str) -> None:
        self._source = source

    def debug(self, message: str) -> None:
        self._emit("debug", 10, message)

    def info(self, message: str) -> None:
        self._emit("info", 20, message)

    def warning(self, message: str) -> None:
        self._emit("warning", 30, message)

    def error(self, message: str) -> None:
        self._emit("error", 40, message)

    def _emit(self, level: str, value: int, message: str) -> None:
        now = time.time()
        trace_id: str | None = None
        provider = _trace_provider
        if provider is not None:
            try:
                trace_id = provider()
            except Exception:
                trace_id = None
        entry = {
            "ts": now,
            "level": level,
            "source": self._source,
            "message": message,
            "trace_id": trace_id,
        }
        with _ring_lock:
            _ring.append(entry)
        sink = _sink
        if sink is not None:
            try:
                sink(entry)
            except Exception:
                pass  # sinks enqueue; a failure here must never take down the caller
        with _config_lock:
            minimum = _MIN
        if value >= minimum:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            sys.stderr.write(f"{stamp} {self._source} {message}\n")
            sys.stderr.flush()


def get_logger(source: str) -> Logger:
    return Logger(source)


def ring_entries(n: int | None = None) -> list[dict]:
    """Ring snapshot, oldest to newest; ``n`` keeps the newest entries."""
    with _ring_lock:
        items = list(_ring)
    if n is not None and n >= 0:
        items = items[-n:] if n else []
    return items


def ring_clear() -> None:
    """Drop all ring entries (tests and the future Logs clear action)."""
    with _ring_lock:
        _ring.clear()
