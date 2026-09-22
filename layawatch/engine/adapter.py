"""Engine adapter seam: the ``EngineAdapter`` protocol and a deterministic fake.

``engine/adapter.py`` is the only place the rest of LayaWatch talks to the decision engine, so the
HTTP layer and the test suite run without torch or a checkpoint download. Phase 0 ships the
protocol and ``FakeAdapter``; the wrapper around the real ``laya.Router`` arrives with the engine
endpoints.

Response shapes follow ``docs/api-reference.md`` section 4 and the real router as exercised by
``scripts/smoke_laya.py`` and ``legacy/serve.py``:

- ``predict`` returns ``{"answers": {question: {"choice", "confidence", "noul"}, ...}, "model",
  "route_reason", "lang"}`` -- answers map every question key to a choice plus confidence/noul
  floats in ``[0.0, 1.0]``.
- ``route`` returns the same routing decision the real ``Router.route()`` produces, without any
  answers: ``{"model", "reason", "lang"}``. The router's native ``reason`` key (evidenced by
  ``res["routing"]["reason"]`` in ``scripts/smoke_laya.py``) is surfaced as ``route_reason`` in
  the predict payload, matching the API reference and the trace column.

``FakeAdapter`` derives every value deterministically from its inputs, so the same input yields
the same output across calls and across threads. One ``threading.Lock`` guards the call counters,
the loaded-model set and the error-injection budget.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EngineAdapter(Protocol):
    """The engine surface the HTTP layer, health endpoint and tests depend on."""

    def predict(
        self,
        state: Any,
        questions: Any,
        *,
        model: str | None = None,
        task: str | None = None,
    ) -> dict:
        """Answer ``questions`` about ``state``; returns the predict shape (see module docs)."""
        ...

    def route(self, state: Any, questions: Any) -> dict:
        """Return the routing decision for ``state`` without running a forward pass."""
        ...

    def loaded(self) -> list[str]:
        """Return the loaded checkpoint names, sorted."""
        ...

    def load(self, models: list[str]) -> None:
        """Load checkpoints; an unknown name raises ``ValueError`` naming it."""
        ...

    def unload(self, models: list[str]) -> None:
        """Unload checkpoints; names that are not loaded are a no-op."""
        ...

    def device(self) -> str:
        """Return the device label surfaced by ``GET /healthz``."""
        ...


def _stable(value: Any) -> str:
    """Canonical JSON so hashing is independent of dict ordering and pretty-printing."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


class FakeAdapter:
    """Deterministic, thread-safe ``EngineAdapter`` for tests and engine-less runs.

    Keyword-only knobs:

    - ``models``: the known checkpoint catalog; ``load`` rejects names outside it.
    - ``latency_ms``: sleep applied at the start of every ``predict``/``route`` call when > 0.
    - ``error`` + ``fail_times``: raise ``error`` from the next ``fail_times`` ``predict`` and
      ``route`` calls (one shared budget, so counting recovers across both methods);
      ``fail_times=-1`` fails forever, ``0`` never fails.
    - ``initially_loaded``: models seeded into the loaded set; default is none loaded, and every
      seeded name must be known.

    ``predict``/``route`` decisions are derived from the serialized state: ASCII states route as
    English (``lang="en"``), anything else as multilingual (``lang="mul"``), and an explicit
    ``model`` argument overrides the chosen checkpoint while keeping the language detection.
    """

    def __init__(
        self,
        *,
        models: Sequence[str] = ("english",),
        latency_ms: float = 0.0,
        error: Exception | None = None,
        fail_times: int = 0,
        initially_loaded: Sequence[str] = (),
    ) -> None:
        known = list(dict.fromkeys(models))
        if not known:
            raise ValueError("models must name at least one checkpoint")
        unknown = [name for name in initially_loaded if name not in known]
        if unknown:
            raise ValueError(f"unknown model(s) in initially_loaded: {', '.join(unknown)}")
        if fail_times != 0 and error is None:
            raise ValueError("fail_times requires an error to inject")
        self._known = tuple(known)
        self._latency_ms = latency_ms
        self._error = error
        self._fail_remaining = fail_times
        self._lock = threading.Lock()
        self._loaded: set[str] = set(initially_loaded)
        self._counters = {"predict": 0, "route": 0, "loaded": 0, "load": 0, "unload": 0, "device": 0}

    def predict(
        self,
        state: Any,
        questions: Any,
        *,
        model: str | None = None,
        task: str | None = None,
    ) -> dict:
        self._bump("predict")
        self._gate()
        chosen, reason, lang = self._decide(state, model)
        answers = {name: self._answer(state, name, spec) for name, spec in questions.items()}
        return {"answers": answers, "model": chosen, "route_reason": reason, "lang": lang}

    def route(self, state: Any, questions: Any) -> dict:
        self._bump("route")
        self._gate()
        chosen, reason, lang = self._decide(state, None)
        return {"model": chosen, "reason": reason, "lang": lang}

    def loaded(self) -> list[str]:
        self._bump("loaded")
        with self._lock:
            return sorted(self._loaded)

    def load(self, models: list[str]) -> None:
        self._bump("load")
        unknown = [name for name in models if name not in self._known]
        if unknown:
            raise ValueError(f"unknown model(s): {', '.join(unknown)}")
        with self._lock:
            self._loaded.update(models)

    def unload(self, models: list[str]) -> None:
        self._bump("unload")
        with self._lock:
            for name in models:
                self._loaded.discard(name)

    def device(self) -> str:
        self._bump("device")
        return "fake"

    def counters(self) -> dict[str, int]:
        """Return call counts per method; failed ``predict``/``route`` calls count too."""
        with self._lock:
            return dict(self._counters)

    def _bump(self, name: str) -> None:
        with self._lock:
            self._counters[name] += 1

    def _gate(self) -> None:
        """Apply the configured latency, then the shared predict/route error budget."""
        if self._latency_ms > 0:
            time.sleep(self._latency_ms / 1000.0)
        with self._lock:
            remaining = self._fail_remaining
            if remaining > 0:
                self._fail_remaining = remaining - 1
            failure = self._error if remaining != 0 else None
        if failure is not None:
            raise failure

    def _decide(self, state: Any, override: str | None) -> tuple[str, str, str]:
        ascii_state = _stable(state).isascii()
        lang = "en" if ascii_state else "mul"
        if override is not None:
            return override, f"model override: {override}", lang
        preferred = "english" if ascii_state else "multilingual"
        model = preferred if preferred in self._known else self._known[0]
        reason = "state is English" if ascii_state else "state is not English"
        return model, reason, lang

    def _answer(self, state: Any, name: str, spec: Any) -> dict:
        seed = hashlib.sha256(f"{name}\x00{_stable(spec)}\x00{_stable(state)}".encode()).digest()
        n = int.from_bytes(seed[:8], "big")
        criteria = spec.get("criteria") if isinstance(spec, dict) else None
        if isinstance(criteria, dict) and criteria:
            choice: Any = list(criteria)[n % len(criteria)]
        elif isinstance(criteria, list) and criteria:
            choice = criteria[n % len(criteria)]
        else:
            choice = None
        return {
            "choice": choice,
            "confidence": round((n % 10001) / 10000.0, 4),
            "noul": round(((n >> 16) % 10001) / 10000.0, 4),
        }
