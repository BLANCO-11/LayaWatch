"""Engine adapter seam: the ``EngineAdapter`` protocol and a deterministic fake.

``engine/adapter.py`` is the only place the rest of LayaWatch talks to the decision engine, so the
HTTP layer and the test suite run without torch or a checkpoint download. Phase 0 ships the
protocol and ``FakeAdapter``; Phase 1 adds ``RealAdapter``, the instrumented wrapper around the
real ``laya.Router`` (see its class docstring).

Response shapes follow ``docs/api-reference.md`` section 4 and the real router as exercised by
``scripts/smoke_laya.py`` and ``legacy/serve.py``:

- ``FakeAdapter.predict`` (the API-facing shape the HTTP layer serves) returns ``{"answers":
  {question: {"choice", "confidence", "noul"}, ...}, "model", "route_reason", "lang"}`` -- answers
  map every question key to a choice plus confidence/noul floats in ``[0.0, 1.0]``.
- ``FakeAdapter.route`` returns the same routing decision the real ``Router.route()`` produces,
  without any answers: ``{"model", "reason", "lang"}``. The router's native ``reason`` key
  (evidenced by ``res["routing"]["reason"]`` in ``scripts/smoke_laya.py``) is surfaced as
  ``route_reason`` in the predict payload, matching the API reference and the trace column.
- ``RealAdapter`` maps the router-native payloads onto those same shapes so the handler never
  needs to know which adapter is wired: predict ``{"answers", "model", "route_reason", "lang",
  "usage"}`` (``usage`` is a passthrough extra), route ``{"model", "reason", "lang"}``, with
  ``model`` from ``routing.model``, ``route_reason`` from ``routing.reason`` and ``lang`` from
  detection. The trace gets ``route_reason``/``lang`` through the spans sink, keeping both key
  names per plan/phase-0-foundation/decisions.md D-007.

``FakeAdapter`` derives every value deterministically from its inputs, so the same input yields
the same output across calls and across threads. One ``threading.Lock`` guards the call counters,
the loaded-model set and the error-injection budget.
"""
from __future__ import annotations

import hashlib
import json
import os
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


# ------------------------------------------------------------------- real engine
# Memory floor per checkpoint: MemAvailable must cover GB_PER_CHECKPOINT for every checkpoint a
# load() would newly build. 1.5 GiB = the ~1.3 GB one checkpoint actually costs in RSS
# (legacy/serve.py measures "~1.3 GB RAM saved" by dropping multilingual) plus ~0.2 GiB of
# headroom for tokenizer and activation buffers; laya's three checkpoints are "~1.16B parameters"
# together (laya/router.py docstring), i.e. ~1.5 GB of fp32 weights each. Checked against
# MemAvailable (what the kernel can still hand out), not MemTotal.
GB_PER_CHECKPOINT = 1.5 * 1024**3


class EngineMemoryError(RuntimeError):
    """MemAvailable cannot cover the checkpoints a load would build; see GB_PER_CHECKPOINT."""


class EnglishOnlyError(ValueError):
    """An english-only deployment refused the state or a multilingual override (legacy 422).

    Subclasses ValueError so a handler carrying the legacy ValueError -> 400 mapping still fails
    closed; catch this class first to answer 422 the way legacy/serve.py did. ``detection`` is the
    analyse() subset legacy put in the 422 body (None when an override triggered the refusal).
    """

    def __init__(self, message: str, detection: dict | None = None) -> None:
        super().__init__(message)
        self.detection = detection


def _mem_available() -> int | None:
    """MemAvailable bytes from /proc/meminfo; None when unreadable (the floor check then passes)."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, IndexError, ValueError):
        return None
    return None


def _hf_cache_root() -> str:
    """Hugging Face hub cache root, mirroring huggingface_hub's environment precedence."""
    env = os.environ
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if env.get(name):
            return env[name]
    if env.get("HF_HOME"):
        return os.path.join(env["HF_HOME"], "hub")
    xdg = env.get("XDG_CACHE_HOME") or os.path.join(env.get("HOME", ""), ".cache")
    return os.path.join(xdg, "huggingface", "hub")


def _checkpoint_location(repo: str) -> tuple[str, int | None]:
    """Where a checkpoint would load from: ("disk", snapshot bytes) or ("hub", None).

    Probes only the local hub cache for ``repo`` (org/name); never touches the network. A partial
    snapshot still counts as disk -- the hub finishes the fetch without a cold download.
    """
    org, sep, name = repo.partition("/")
    if not sep:
        return "hub", None
    snapshots = os.path.join(_hf_cache_root(), f"models--{org}--{name}", "snapshots")
    total = 0
    found = False
    for dirpath, _dirnames, filenames in os.walk(snapshots):
        for filename in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, filename))
            except OSError:
                continue
            found = True
    return ("disk", total) if found else ("hub", None)


def _load_attrs(router: Any, name: str) -> dict[str, Any]:
    """Attributes for a ``model.load`` span around building ``name``.

    ``from`` is "disk" when the hub cache already holds the repo snapshot (with its byte size),
    "hub" otherwise. The vocabulary's "preloaded" value never fires: these spans wrap only
    checkpoints that are actually about to load.
    """
    spec: Any = router.models.get(name)
    if isinstance(spec, (list, tuple)):
        spec = spec[0] if spec else None
    where, nbytes = _checkpoint_location(str(spec)) if spec else ("hub", None)
    attrs: dict[str, Any] = {"model": name, "from": where}
    if nbytes is not None:
        attrs["bytes"] = nbytes
    return attrs


def _lang_code(det: dict) -> str:
    """Language code for the trace and lang.detect span: detected language, else en/mul."""
    language = det.get("language")
    if isinstance(language, str) and language:
        return language
    return "en" if det.get("is_english") else "mul"


def _detect_confidence(det: dict) -> float:
    """Confidence for lang.detect: 1.0 once a language is named, 0.5 while laya is undecided.

    analyse() exposes no probability, so an undecided detection is recorded as a coin flip rather
    than an invented score; the routing decision itself (is_english) is separate and always known.
    """
    return 0.5 if det.get("language_undecided") else 1.0


class _NullSpan:
    """Context manager handed out by the spans=None fast path."""

    __slots__ = ()

    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _NullSpans:
    """Same surface as the recorder context (span/set/event); performs no work."""

    __slots__ = ()

    def span(self, name: str, **attrs: Any) -> _NullSpan:
        return _NULL_SPAN

    def event(self, name: str, **attrs: Any) -> None:
        return None

    def set(self, **fields: Any) -> None:
        return None


_NULL_SPAN = _NullSpan()
_NULL_SPANS = _NullSpans()


class RealAdapter:
    """EngineAdapter over the real ``laya.Router``, constructed exactly as ``legacy/serve.py`` did.

    Nothing touches laya or torch until the first predict/route/load call; that first call
    reaches the same end state as the legacy module import, but observable: ``Router(device=...)``
    is always built cold (``preload=False``) and every requested checkpoint loads inside its own
    ``model.load`` span, so a first request's cold load lands in the trace instead of inflating
    ``framework_ms``. An exact ``["all"]`` list expands to the whole catalog; any other list is
    normalised through ``engine.router.normalise_name`` when the engine surface exposes it (the
    configured name passes through otherwise -- ``Router.preload`` still validates it and a
    ValueError names an unknown checkpoint, as the legacy ``preload(models)`` call did), and the
    multilingual pop under english_only still runs after the preload. Checkpoints the router
    already reports as loaded are skipped entirely: no span, no call -- ``model.load`` records
    loads, not warm routers. The LRU cap ``max_loaded`` is raised to the full list before the
    per-checkpoint loop because laya's ``Router.preload`` only raises it per call -- loading one
    name at a time would otherwise evict the checkpoints built moments earlier; the end state
    matches one legacy ``preload(list)`` call. Callers wanting a warm start touch the adapter at
    boot; loaded()/device() never construct the engine, so ``GET /healthz`` can never trigger a
    model download.

    Spans, through the injected ``spans`` object (None = internal no-op; the object also exposes
    ``set`` and ``event``, so it can be the recorder context itself or a per-thread dispatcher):

    - predict: ``lang.detect``, ``route.decide``, ``queue.wait``, ``forward``, ``serialize`` in
      that order -- the five warm-engine spans. ``model.load`` appears only when a checkpoint is
      actually built: during first-use construction (before ``lang.detect``, one span per
      checkpoint, on whichever request thread triggered the construction) or, for a routed model
      outside the configured preload list, between ``queue.wait`` and ``forward`` via
      ``_resident_agent``. Detection and routing run before the serialization lock; the lock
      guards load + forward + serialize, which is exactly the forward-pass serialization
      legacy's ``_predict_lock`` existed for (routing stays parallel, as it always was on the
      lock-free ``/route`` path).
    - route: ``lang.detect`` and ``route.decide`` only; no lock, like legacy ``/route``.

    The recorder validates span attributes when a span opens, so spans whose attributes are the
    *output* of the work they time (``lang.detect``, ``route.decide``) run that work twice: once
    for the result and attributes, once inside the timed span. Both passes are pure and cost
    microseconds (docs/architecture.md section 3) against a forward pass of hundreds of ms.

    Results are the uniform api-reference shapes shared with FakeAdapter (module docs): predict
    returns answers/model/route_reason/lang plus the engine's ``usage`` passthrough; route
    returns model/reason/lang. Trace fields model, route_reason, lang, queue_ms and forward_ms are
    written through ``spans.set`` as they become known.

    english_only replicates legacy's pre-engine 422 checks and raises :class:`EnglishOnlyError`
    after ``lang.detect`` has been recorded, so a refusal trace has detection but no forward.
    """

    def __init__(
        self,
        *,
        engine: Any = None,
        spans: Any = None,
        device: str | None = None,
        models: Sequence[str] | None = None,
        english_only: bool | None = None,
        state_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._engine = engine
        self._spans = _NULL_SPANS if spans is None else spans
        if device is None:
            device = os.environ.get("LAYA_DEVICE") or None
        # "auto" is the config spelling of legacy's unset LAYA_DEVICE: let torch choose.
        if device is not None and str(device).strip().lower() == "auto":
            device = None
        self._device = device
        if models is None:
            models = [
                m.strip()
                for m in os.environ.get("LAYA_MODELS", "english,multilingual").split(",")
                if m.strip()
            ]
        self._models = list(models)
        if english_only is None:
            flag = os.environ.get("LAYA_ENGLISH_ONLY", "").strip().lower()
            english_only = flag in ("1", "true", "yes", "on")
        self._english_only = bool(english_only)
        self._state_dir = None if state_dir is None else os.fspath(state_dir)
        self._router: Any = None
        self._ensure_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()  # one admin load/unload in flight
        self._predict_lock = threading.Lock()  # legacy _predict_lock: one forward pass
        self._inflight_lock = threading.Lock()
        self._inflight = 0

    def predict(
        self,
        state: Any,
        questions: Any,
        *,
        model: str | None = None,
        task: str | None = None,
    ) -> dict:
        router = self._ensure()
        decision, lang_code = self._decide(router, state, questions, model, task)
        with self._inflight_lock:
            self._inflight += 1
            depth = self._inflight - 1
        try:
            opened = time.perf_counter()
            with self._spans.span("queue.wait", depth_at_acquire=depth):
                self._predict_lock.acquire()
            self._spans.set(queue_ms=(time.perf_counter() - opened) * 1000.0)
            try:
                agent = self._resident_agent(router, decision)
                started = time.perf_counter()
                with self._spans.span(
                    "forward",
                    model=decision["model"],
                    questions=len(questions),
                    device=str(router.device or "auto"),
                ):
                    try:
                        result = agent.system_one(state, questions)
                    except KeyError as exc:
                        # P1-I2: engine schema gaps (question missing "type") are
                        # client errors: ValueError -> _guard answers 400, not 500.
                        raise ValueError(
                            f"question payload missing a required field: {exc}"
                        ) from exc
                self._spans.set(forward_ms=(time.perf_counter() - started) * 1000.0)
                with self._spans.span("serialize", answers=len(result["answers"])):
                    return {
                        "answers": result["answers"],
                        "model": decision["model"],
                        "route_reason": decision["reason"],
                        "lang": lang_code,
                        "usage": result.get("usage"),
                    }
            finally:
                self._predict_lock.release()
        finally:
            with self._inflight_lock:
                self._inflight -= 1

    def route(self, state: Any, questions: Any) -> dict:
        router = self._ensure()
        decision, lang_code = self._decide(router, state, questions, None, None)
        return {"model": decision["model"], "reason": decision["reason"], "lang": lang_code}

    def loaded(self) -> list[str]:
        """Sorted resident checkpoint names; [] before first engine use (no construction)."""
        router = self._router
        return [] if router is None else sorted(router.loaded)

    def load(self, models: list[str]) -> None:
        """Load checkpoints through Router.preload under the single-in-flight lifecycle guard.

        ValueError names unknown (or english_only-blocked) models, EngineMemoryError fires when
        MemAvailable cannot cover the checkpoints that are not resident yet, and RuntimeError
        fires when another load/unload is already running (mapped to 409 by the API layer).
        """
        self._acquire_lifecycle()
        try:
            router = self._ensure()
            names = [self._engine.router.normalise_name(name) for name in models]
            unknown = [name for name in names if name not in router.models]
            if unknown:
                raise ValueError(f"unknown model(s): {', '.join(unknown)}")
            fresh = [name for name in dict.fromkeys(names) if name not in router.loaded]
            if fresh:
                available = _mem_available()
                needed = len(fresh) * GB_PER_CHECKPOINT
                if available is not None and available < needed:
                    raise EngineMemoryError(
                        f"loading {len(fresh)} checkpoint(s) needs {needed / 1024**3:.1f} GB "
                        f"available memory; MemAvailable is {available / 1024**3:.1f} GB"
                    )
            router.preload(names)
        finally:
            self._lifecycle_lock.release()

    def unload(self, models: list[str]) -> None:
        """Unload checkpoints under the lifecycle guard; resident names not listed are untouched."""
        self._acquire_lifecycle()
        try:
            router = self._ensure()
            for name in models:
                router.unload(name)
        finally:
            self._lifecycle_lock.release()

    def device(self) -> str:
        """Device label for GET /healthz: configured device, router's device, else "auto"."""
        router = self._router
        device = self._device if router is None else router.device
        return str(device or "auto")

    def _ensure(self) -> Any:
        """Construct the Router on first engine use; laya is imported inside, never above.

        Always builds with ``preload=False`` and loads each checkpoint inside its own
        ``model.load`` span, so the potentially minutes-long cold load that happens to fall on
        the first request is attributed to real observations instead of framework overhead.
        """
        router = self._router
        if router is not None:
            return router
        with self._ensure_lock:
            if self._router is not None:
                return self._router
            engine = self._engine
            if engine is None:
                import laya

                engine = self._engine = laya
            requested = ["english"] if self._english_only else list(self._models)
            router = engine.Router(device=self._device, preload=False)
            if requested == ["all"]:
                names = list(router.models)  # catalog keys are already canonical
            else:
                normalise = getattr(getattr(engine, "router", None), "normalise_name", None)
                names = [name if normalise is None else normalise(name) for name in requested]
            # Only checkpoints that are not resident yet actually load -- and a vocabulary
            # model.load fires "only when a checkpoint loads", never for an already-warm router.
            to_load = [name for name in names if name not in router.loaded]
            if to_load:
                # preload() only ever raises max_loaded to fit its own argument list, so loading
                # one checkpoint per call would leave the LRU cap at 1 and evict the checkpoints
                # built a moment earlier. Raise it to the full list first: same end state as one
                # legacy preload(names) call.
                router.max_loaded = max(int(getattr(router, "max_loaded", 1)), len(names))
                for name in to_load:
                    with self._spans.span("model.load", **_load_attrs(router, name)):
                        router.preload([name])
            if self._english_only:
                router.models.pop("multilingual", None)  # belt: explicit model= cannot load it
            self._router = router
            return router

    def _decide(
        self,
        router: Any,
        state: Any,
        questions: Any,
        model: str | None,
        task: str | None,
    ) -> tuple[dict, str]:
        """Detect, refuse under english_only, route; emit both spans and set the trace fields.

        The decision used is the first, untimed route pass; the timed second pass must be pure --
        Router.route loads and runs nothing (laya/router.py: "without loading or running
        anything").
        """
        engine = self._engine
        det = engine.lang.analyse(state)
        lang_code = _lang_code(det)
        with self._spans.span("lang.detect", lang=lang_code, confidence=_detect_confidence(det)):
            engine.lang.analyse(state)  # timed pass; attributes must exist before the span opens
        self._refuse_when_english_only(det, model, engine)
        decision = router.route(state, questions, model=model, task=task)
        with self._spans.span(
            "route.decide",
            model=decision["model"],
            reason=decision["reason"],
            typed_workflow=bool(decision.get("workflow")),
        ):
            router.route(state, questions, model=model, task=task)  # timed pass; see docstring
        self._spans.set(
            model=decision["model"],
            route_reason=decision["reason"],
            lang=lang_code,
        )
        return decision, lang_code

    def _refuse_when_english_only(self, det: dict, model: str | None, engine: Any) -> None:
        """Replicate legacy/serve.py's pre-engine 422 checks under LAYA_ENGLISH_ONLY."""
        if not self._english_only:
            return
        if not det.get("is_english"):
            raise EnglishOnlyError(
                "english-only deployment: state did not detect as English",
                detection={
                    key: det.get(key)
                    for key in ("script", "language", "non_latin_fraction", "diacritic_rate")
                },
            )
        wants_multi = False
        if model:
            try:
                wants_multi = engine.router.normalise_name(model) == "multilingual"
            except ValueError:
                wants_multi = False  # unknown model name: let route() answer ValueError -> 400
        if wants_multi:
            raise EnglishOnlyError("english-only deployment: multilingual checkpoint not available")

    def _resident_agent(self, router: Any, decision: dict) -> Any:
        """Return the routed checkpoint's agent, timing model.load only on a real cold load.

        This on-demand site can fire when the routed checkpoint sits outside the configured
        preload list (e.g. multilingual on an english-default deployment), so it keeps its own
        span; a fully preloaded catalog never loads here.
        """
        name = decision["model"]
        if name in router.loaded:
            return router.load(name)
        with self._spans.span("model.load", **_load_attrs(router, name)):
            return router.load(name)

    def _acquire_lifecycle(self) -> None:
        """Take the single load/unload slot, or fail loudly (the API layer maps this to 409)."""
        if not self._lifecycle_lock.acquire(blocking=False):
            raise RuntimeError(
                "a model load or unload is already in flight; retry when it finishes"
            )
