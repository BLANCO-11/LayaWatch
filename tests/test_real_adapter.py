"""RealAdapter contract tests: legacy construction, span emission, guards -- all with a stub engine.

Nothing here imports laya or torch: the stub engine mimics the exact laya surface RealAdapter uses
(``Router(device=, preload=)``, ``router.preload/unload/loaded/device/models/route``,
``lang.analyse``, ``router.normalise_name``) and the result payloads mirror what laya 0.3.5
returns (system_one's ``model``/``answers``/``usage`` plus the ``routing`` decision keys).
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import layawatch.engine.adapter as adapter_mod
from layawatch.engine.adapter import (
    EngineAdapter,
    EnglishOnlyError,
    RealAdapter,
)
from layawatch.obs import vocabulary
from layawatch.obs.recorder import Recorder

REPO_ROOT = Path(__file__).resolve().parent.parent

STATE = "Hello team, please refund the duplicate charge today."
# Non-ASCII source text via escapes: keeps this file ASCII while detecting as non-English.
STATE_NON_ENGLISH = (
    "\u092e\u0941\u091c\u0932\u0938\u0947 \u091a\u093e\u0930\u091c"
    " \u0935\u093e\u092a\u0938"
)

QUESTIONS = {
    "department": {
        "type": "choice",
        "instructions": "Route the ticket",
        "criteria": {"billing": None, "support": None},
    },
    "churn_risk": {"type": "noul", "instructions": "Churn likelihood"},
}

CATALOG = {
    "english": ("convaiinnovations/laya", None),
    "multilingual": ("convaiinnovations/laya", "multilingual"),
    "typed-decisions": ("convaiinnovations/laya", "typed-decisions"),
}

ENGLISH_DET = {
    "script": "latin",
    "script_profile": {"latin": 1.0},
    "language": "en",
    "is_english": True,
    "language_undecided": False,
    "diacritic_rate": 0.0,
    "non_latin_fraction": 0.0,
}
NON_ENGLISH_DET = {
    "script": "Devanagari",
    "script_profile": {"latin": 0.0},
    "language": None,
    "is_english": False,
    "language_undecided": True,
    "diacritic_rate": 0.0,
    "non_latin_fraction": 1.0,
}

EXPECTED_ANSWERS = {
    "department": {
        "type": "choice",
        "choice": "billing",
        "probabilities": {"billing": 0.75, "support": 0.25},
        "confidence": 0.75,
        "action": None,
    },
    "churn_risk": {"type": "noul", "noul": 0.12, "confidence": 0.88, "action": None},
}
EXPECTED_USAGE = {"input_tokens": 12, "output_tokens": 0}


def fake_analyse(state: object) -> dict:
    """laya.lang.analyse stand-in: ASCII text is English, anything else is non-Latin/undecided."""
    text = state if isinstance(state, str) else str(state)
    return dict(ENGLISH_DET) if text.isascii() else dict(NON_ENGLISH_DET)


_ALIASES = {"en": "english", "laya": "english", "multi": "multilingual"}


def fake_normalise_name(name: object) -> str:
    """laya.router.normalise_name stand-in: same aliasing and the same ValueError wording."""
    key = str(name).strip().lower()
    key = _ALIASES.get(key, key)
    if key not in CATALOG:
        raise ValueError(
            "unknown model %r; choose one of %s" % (name, sorted(CATALOG))
        )
    return key


def repo_str(spec: object) -> str:
    if isinstance(spec, (list, tuple)):
        repo, *rest = list(spec) + [None]
        return f"{repo}/{rest[0]}" if rest[0] else str(repo)
    return str(spec)


class StubAgent:
    """system_one stand-in with laya's real answer shapes and a controllable forward pass."""

    def __init__(self, engine: StubEngine, name: str) -> None:
        self.engine = engine
        self.name = name

    def system_one(self, state: object, questions: dict) -> dict:
        if self.engine.forward_hook is not None:
            self.engine.forward_hook()
        answers: dict = {}
        for qid, qdef in questions.items():
            if qdef.get("type") == "noul":
                answers[qid] = {
                    "type": "noul",
                    "noul": 0.12,
                    "confidence": 0.88,
                    "action": None,
                }
            else:
                answers[qid] = {
                    "type": "choice",
                    "choice": "billing",
                    "probabilities": {"billing": 0.75, "support": 0.25},
                    "confidence": 0.75,
                    "action": None,
                }
        return {
            "model": "laya-rl-agent",
            "answers": answers,
            "usage": dict(EXPECTED_USAGE),
        }


class StubRouter:
    """laya.Router stand-in: lazy agent construction, model catalog, lock-free routing."""

    def __init__(self, engine: StubEngine, device: str | None = None) -> None:
        self.engine = engine
        self.models = dict(CATALOG)
        self.device = device
        self._order: list[str] = []
        self._agents: dict[str, StubAgent] = {}

    def preload(self, names: list[str] | None = None) -> StubRouter:
        wanted = list(self.models) if names is None else list(names)
        self.engine.preload_calls.append(list(wanted))
        for name in wanted:
            key = fake_normalise_name(name)
            if key not in self.models:
                raise KeyError(key)  # laya hits models[key] the same way after the english pop
            if self.engine.preload_hook is not None:
                self.engine.preload_hook(key)
            if key not in self._agents:
                self._agents[key] = StubAgent(self.engine, key)
                self.engine.built.append(key)
            if key not in self._order:
                self._order.append(key)
        return self

    def load(self, name: str) -> StubAgent:
        key = fake_normalise_name(name)
        if key in self._agents:
            self._order.remove(key)
            self._order.append(key)
            return self._agents[key]
        if key not in self.models:
            raise KeyError(key)
        agent = StubAgent(self.engine, key)
        self._agents[key] = agent
        self._order.append(key)
        self.engine.built.append(key)
        return agent

    def unload(self, name: str | None = None) -> None:
        if name is None:
            self._agents.clear()
            self._order.clear()
            return
        key = fake_normalise_name(name)
        self._agents.pop(key, None)
        if key in self._order:
            self._order.remove(key)

    @property
    def loaded(self) -> list[str]:
        return list(self._order)

    def route(
        self,
        state: object,
        questions: dict | None = None,
        model: str | None = None,
        task: str | None = None,
        lang: str | None = None,
    ) -> dict:
        if model is not None:
            return self._decision(
                fake_normalise_name(model), "explicit model=%r" % model, None
            )
        if task is not None:
            return self._decision(
                fake_normalise_name(task), "explicit task=%r" % task, None
            )
        det = self.engine.lang.analyse(state)
        if det["script"] == "unknown":
            key, reason = "english", "no letters detected in state; using default (english)"
        elif det["script"] != "latin" or not det["is_english"]:
            key, reason = "multilingual", "state does not detect as English"
        else:
            key, reason = "english", "English Latin text"
        return self._decision(key, reason, det)

    def _decision(self, key: str, reason: str, detection: dict | None) -> dict:
        if key not in self.models:
            raise KeyError(key)
        return {
            "model": key,
            "repo": repo_str(self.models[key]),
            "reason": reason,
            "detection": detection,
            "workflow": None,
        }


class StubEngine:
    """laya-like module surface: Router factory, lang.analyse, router.normalise_name."""

    def __init__(self, *, forward_hook=None, preload_hook=None) -> None:
        self.lang = SimpleNamespace(analyse=fake_analyse)
        self.router = SimpleNamespace(normalise_name=fake_normalise_name)
        self.Router = self._make_router
        self.ctor_calls: list[dict] = []
        self.instances: list[StubRouter] = []
        self.built: list[str] = []
        self.preload_calls: list[list[str]] = []
        self.forward_hook = forward_hook
        self.preload_hook = preload_hook

    def _make_router(self, device: str | None = None, preload: bool = False) -> StubRouter:
        self.ctor_calls.append({"device": device, "preload": preload})
        router = StubRouter(self, device=device)
        self.instances.append(router)
        if preload:
            router.preload()
        return router

    @property
    def router_instance(self) -> StubRouter:
        return self.instances[-1]


class ThreadSpans:
    """The span sink shape the middleware will inject: forwards to this thread's Context."""

    def __init__(self) -> None:
        self._local = threading.local()

    def bind(self, ctx) -> None:
        self._local.ctx = ctx

    def span(self, name: str, **attrs):
        return self._local.ctx.span(name, **attrs)

    def event(self, name: str, **attrs):
        return self._local.ctx.event(name, **attrs)

    def set(self, **fields) -> None:
        self._local.ctx.set(**fields)


def start_trace(spans: ThreadSpans, route: str = "/predict"):
    ctx = Recorder().start(route, "POST", client_ip="127.0.0.1")
    spans.bind(ctx)
    return ctx


def make_adapter(engine: StubEngine, **kwargs) -> RealAdapter:
    kwargs.setdefault("models", ["english"])
    kwargs.setdefault("english_only", False)
    kwargs.setdefault("device", "cpu")
    return RealAdapter(engine=engine, **kwargs)


def warm(engine: StubEngine, **kwargs) -> RealAdapter:
    """Construct the engine through a first predict so later calls exercise the warm path."""
    adapter = make_adapter(engine, **kwargs)
    adapter.predict(STATE, QUESTIONS)
    return adapter


def test_module_import_does_not_pull_torch_or_laya() -> None:
    code = (
        "import sys, layawatch.engine.adapter as a; "
        "assert 'torch' not in sys.modules and 'laya' not in sys.modules; "
        "assert hasattr(a, 'RealAdapter'); print('lazy ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert "lazy ok" in out.stdout


def test_real_adapter_satisfies_the_protocol() -> None:
    assert isinstance(RealAdapter(engine=StubEngine()), EngineAdapter)
    assert not isinstance(object(), EngineAdapter)


def test_first_use_constructs_the_legacy_router() -> None:
    engine = StubEngine()
    adapter = make_adapter(engine, models=["english", "multilingual"])
    assert engine.ctor_calls == []  # nothing built at construction time
    adapter.predict(STATE, QUESTIONS)
    assert engine.ctor_calls == [{"device": "cpu", "preload": False}]
    # Section 4.4 item 22: staged loading - the first configured checkpoint loads
    # synchronously (in its own model.load span); the rest wait in the prefetch queue,
    # which only the production startup worker drains (injected engines have none, and
    # _resident_agent still loads a routed gap on demand).
    assert engine.preload_calls == [["english"]]
    assert adapter.loaded() == ["english"]
    assert list(adapter._prefetch_queue) == ["multilingual"]


def test_all_models_spans_each_checkpoint_and_keeps_the_ctor_cold(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-cache"))
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans, models=["all"])
    ctx = start_trace(spans)
    adapter.route(STATE, QUESTIONS)
    trace = ctx.finish(200)

    # The constructor flag stays cold: the first checkpoint loads via Router.preload([name])
    # under a model.load span, so a first request records the load instead of framework_ms.
    assert engine.ctor_calls == [{"device": "cpu", "preload": False}]
    # Section 4.4 item 22: only the first catalog checkpoint loads on this request; the
    # rest stage for background prefetch (the production startup worker drains the queue;
    # this injected engine has none, and route() needed no other checkpoint).
    first, *rest = list(CATALOG)
    assert engine.preload_calls == [[first]]
    names = [obs.name for obs in trace.observations]
    assert names == ["model.load"] + ["lang.detect", "route.decide"]
    load = trace.observations[0]
    assert load.meta == {"model": first, "from": "hub"}
    assert vocabulary.validate("model.load", load.meta) == load.meta
    assert load.parent_id is None
    assert engine.router_instance._order == [first]
    assert list(adapter._prefetch_queue) == rest
    # LRU cap raised to the whole list first, so prefetched loads cannot evict this one.
    assert engine.router_instance.max_loaded >= len(CATALOG)


def test_english_only_preloads_english_and_drops_multilingual() -> None:
    engine = StubEngine()
    adapter = make_adapter(engine, english_only=True, device="auto")
    adapter.predict(STATE, QUESTIONS)
    assert engine.ctor_calls == [{"device": None, "preload": False}]
    assert engine.router_instance._order == ["english"]
    assert "multilingual" not in engine.router_instance.models
    assert "typed-decisions" in engine.router_instance.models  # only multilingual is removed
    with pytest.raises(EnglishOnlyError, match="multilingual checkpoint not available"):
        adapter.predict(STATE, QUESTIONS, model="multilingual")
    with pytest.raises(ValueError, match="multilingual"):
        adapter.load(["multilingual"])


def test_constructor_defaults_read_the_legacy_environment(monkeypatch) -> None:
    monkeypatch.setenv("LAYA_DEVICE", "cuda")
    monkeypatch.setenv("LAYA_MODELS", "english")
    monkeypatch.setenv("LAYA_ENGLISH_ONLY", "1")
    engine = StubEngine()
    adapter = RealAdapter(engine=engine)
    adapter.predict(STATE, QUESTIONS)
    assert engine.ctor_calls == [{"device": "cuda", "preload": False}]
    assert engine.router_instance._order == ["english"]
    assert "multilingual" not in engine.router_instance.models


def test_predict_emits_the_five_spans_in_order_with_valid_attrs() -> None:
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans)
    warm_ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)  # first call constructs + preloads under its own spans
    assert [obs.name for obs in warm_ctx.finish(200).observations][:1] == ["model.load"]
    ctx = start_trace(spans)
    result = adapter.predict(STATE, QUESTIONS)
    trace = ctx.finish(200)
    assert trace is not None

    names = [obs.name for obs in trace.observations]
    assert names == [
        "lang.detect",
        "route.decide",
        "queue.wait",
        "forward",
        "serialize",
    ]
    assert "model.load" not in names  # warm engine: the checkpoint is resident, nothing loads
    detect, decide, queue, forward, serialize = trace.observations
    assert detect.meta == {"lang": "en", "confidence": 1.0}
    assert decide.meta == {
        "model": "english",
        "reason": "English Latin text",
        "typed_workflow": False,
    }
    assert queue.meta == {"depth_at_acquire": 0}  # idle engine: nobody else in flight
    assert forward.meta == {"model": "english", "questions": 2, "device": "cpu"}
    assert serialize.meta == {"answers": 2}
    previous_start = -1.0
    for obs in trace.observations:
        assert vocabulary.validate(obs.name, obs.meta) == obs.meta
        assert obs.parent_id is None
        assert obs.duration_ms >= 0.0
        assert obs.start_ms >= previous_start
        previous_start = obs.start_ms

    assert set(result) == {"answers", "model", "route_reason", "lang", "usage"}
    assert result == {
        "answers": EXPECTED_ANSWERS,
        "model": "english",
        "route_reason": "English Latin text",
        "lang": "en",
        "usage": EXPECTED_USAGE,
    }
    assert trace.model == "english"
    assert trace.route_reason == "English Latin text"
    assert trace.lang == "en"
    assert trace.queue_ms >= 0.0
    assert trace.forward_ms is not None and trace.forward_ms > 0.0


def test_route_emits_detection_and_decision_and_sets_trace_fields() -> None:
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans)
    warm_ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)  # construct the engine first; its load spans stay there
    warm_ctx.finish(200)
    ctx = start_trace(spans, route="/route")
    result = adapter.route(STATE, QUESTIONS)
    trace = ctx.finish(200)
    assert trace is not None

    assert [obs.name for obs in trace.observations] == ["lang.detect", "route.decide"]
    assert trace.observations[0].meta == {"lang": "en", "confidence": 1.0}
    assert trace.observations[1].meta == {
        "model": "english",
        "reason": "English Latin text",
        "typed_workflow": False,
    }
    assert result == {"model": "english", "reason": "English Latin text", "lang": "en"}
    assert trace.model == "english"
    assert trace.route_reason == "English Latin text"
    assert trace.lang == "en"
    assert trace.queue_ms == 0.0  # /route never touches the serialization lock
    assert trace.forward_ms is None


def test_non_english_state_routes_to_multilingual_with_lang_mul() -> None:
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans)
    warm_ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)  # construct the engine first; its load spans stay there
    warm_ctx.finish(200)
    ctx = start_trace(spans, route="/route")
    result = adapter.route(STATE_NON_ENGLISH, QUESTIONS)
    trace = ctx.finish(200)
    assert trace is not None

    assert result == {
        "model": "multilingual",
        "reason": "state does not detect as English",
        "lang": "mul",
    }
    assert trace.observations[0].meta == {"lang": "mul", "confidence": 0.5}
    assert trace.lang == "mul"


def test_queue_wait_measures_a_contended_lock() -> None:
    entered = threading.Event()
    release = threading.Event()

    def hold_forward() -> None:
        entered.set()
        assert release.wait(10), "test never released the forward pass"

    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans)
    warm_ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)
    warm_ctx.finish(200)
    engine.forward_hook = hold_forward

    outcomes: dict = {}

    def first_call() -> None:
        ctx = start_trace(spans)
        adapter.predict(STATE, QUESTIONS)
        outcomes["first"] = ctx.finish(200)

    def second_call() -> None:
        assert entered.wait(10)
        second_going.set()
        ctx = start_trace(spans)
        adapter.predict(STATE, QUESTIONS)
        outcomes["second"] = ctx.finish(200)

    second_going = threading.Event()
    first = threading.Thread(target=first_call, name="first-predict")
    second = threading.Thread(target=second_call, name="second-predict")
    first.start()
    assert entered.wait(10)  # first caller holds the lock inside forward
    second.start()
    assert second_going.wait(10)
    time.sleep(0.15)  # the second caller is now blocked in queue.wait
    release.set()
    first.join(10)
    second.join(10)
    assert not first.is_alive() and not second.is_alive()

    first_trace = outcomes["first"]
    second_trace = outcomes["second"]
    first_queue = next(o for o in first_trace.observations if o.name == "queue.wait")
    second_queue = next(o for o in second_trace.observations if o.name == "queue.wait")
    assert first_queue.meta == {"depth_at_acquire": 0}
    assert second_queue.meta == {"depth_at_acquire": 1}  # the holder was in flight
    assert first_queue.duration_ms >= 0.0
    assert second_queue.duration_ms > 5.0  # genuinely blocked while the holder ran forward
    assert second_trace.queue_ms > 5.0
    for trace in (first_trace, second_trace):
        assert [o.name for o in trace.observations] == [
            "lang.detect",
            "route.decide",
            "queue.wait",
            "forward",
            "serialize",
        ]


def test_route_does_not_wait_for_the_predict_lock() -> None:
    entered = threading.Event()
    release = threading.Event()

    def hold_forward() -> None:
        entered.set()
        assert release.wait(10)

    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans)
    warm_ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)
    warm_ctx.finish(200)
    engine.forward_hook = hold_forward

    holder_outcome: dict = {}

    def holder() -> None:
        ctx = start_trace(spans)
        adapter.predict(STATE, QUESTIONS)
        holder_outcome["trace"] = ctx.finish(200)

    route_outcome: dict = []

    def router_call() -> None:
        ctx = start_trace(spans, route="/route")
        route_outcome.append(adapter.route(STATE, QUESTIONS))
        ctx.finish(200)

    holder_thread = threading.Thread(target=holder)
    holder_thread.start()
    assert entered.wait(10)  # holder is inside forward, lock taken
    route_thread = threading.Thread(target=router_call)
    route_thread.start()
    route_thread.join(5)
    route_finished_while_locked = not route_thread.is_alive()
    release.set()
    holder_thread.join(10)
    assert route_finished_while_locked, "route blocked on the predict lock"
    assert not route_thread.is_alive()
    assert not holder_thread.is_alive()
    assert route_outcome == [{"model": "english", "reason": "English Latin text", "lang": "en"}]


def test_cold_predict_records_model_load_between_queue_and_forward(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-cache"))
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans, models=[])
    ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)
    trace = ctx.finish(200)
    assert trace is not None

    assert [obs.name for obs in trace.observations] == [
        "lang.detect",
        "route.decide",
        "queue.wait",
        "model.load",
        "forward",
        "serialize",
    ]
    load = trace.observations[3]
    assert load.meta == {"model": "english", "from": "hub"}
    assert vocabulary.validate("model.load", load.meta) == load.meta
    assert engine.preload_calls == []  # construction loaded nothing: this span is on-demand
    assert adapter.loaded() == ["english"]
    assert engine.built == ["english"]


def test_cold_predict_emits_model_load_before_lang_detect(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-cache"))
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans)  # configured ["english"], not yet constructed
    ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)
    trace = ctx.finish(200)
    assert trace is not None

    # First request pays the cold load: it is recorded, not swallowed as framework overhead.
    names = [obs.name for obs in trace.observations]
    assert names == [
        "model.load",
        "lang.detect",
        "route.decide",
        "queue.wait",
        "forward",
        "serialize",
    ]
    load = trace.observations[0]
    assert load.meta == {"model": "english", "from": "hub"}
    assert vocabulary.validate("model.load", load.meta) == load.meta
    assert load.parent_id is None and load.duration_ms >= 0.0

    # The second request on the now-warm engine emits no model.load at all.
    warm_ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)
    warm_names = [obs.name for obs in warm_ctx.finish(200).observations]
    assert "model.load" not in warm_names
    assert warm_names == [
        "lang.detect",
        "route.decide",
        "queue.wait",
        "forward",
        "serialize",
    ]


def test_cold_route_emits_model_load_before_lang_detect(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-cache"))
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans)
    ctx = start_trace(spans, route="/route")
    adapter.route(STATE, QUESTIONS)
    trace = ctx.finish(200)
    assert trace is not None

    names = [obs.name for obs in trace.observations]
    assert names == ["model.load", "lang.detect", "route.decide"]
    load = trace.observations[0]
    assert load.meta == {"model": "english", "from": "hub"}
    assert vocabulary.validate("model.load", load.meta) == load.meta
    assert load.parent_id is None


def test_model_load_reports_disk_and_bytes_when_the_cache_is_local(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    snapshot = cache / "models--convaiinnovations--laya" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"0123456789")  # 10 bytes, hand-computed
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans, models=[])
    ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)
    trace = ctx.finish(200)
    assert trace is not None

    load = next(o for o in trace.observations if o.name == "model.load")
    assert load.meta == {"model": "english", "from": "disk", "bytes": 10}


def test_second_concurrent_load_raises_and_unload_is_guarded_too(monkeypatch) -> None:
    monkeypatch.setattr(adapter_mod, "_mem_available", lambda: 4 * adapter_mod.GB_PER_CHECKPOINT)
    engine = StubEngine()
    adapter = warm(engine)  # constructs and preloads ["english"] before the hook exists
    started = threading.Event()
    hold = threading.Event()

    def block(key: str) -> None:
        started.set()
        assert hold.wait(10), "test never released the first load"

    engine.preload_hook = block
    outcome: dict = {}

    def first_load() -> None:
        try:
            adapter.load(["multilingual"])
            outcome["first"] = "ok"
        except Exception as exc:  # pragma: no cover - would fail the assertion below
            outcome["first"] = exc

    loader = threading.Thread(target=first_load)
    loader.start()
    assert started.wait(10)  # first load is inside Router.preload, lifecycle slot taken

    with pytest.raises(RuntimeError, match="in flight"):
        adapter.load(["typed-decisions"])
    with pytest.raises(RuntimeError, match="in flight"):
        adapter.unload(["english"])

    hold.set()
    loader.join(10)
    assert not loader.is_alive()
    assert outcome["first"] == "ok"
    assert adapter.loaded() == ["english", "multilingual"]


def test_load_refuses_below_the_memory_floor_and_passes_when_roomy(monkeypatch) -> None:
    engine = StubEngine()
    adapter = warm(engine)
    monkeypatch.setattr(
        adapter_mod, "_mem_available", lambda: int(adapter_mod.GB_PER_CHECKPOINT) - 1
    )
    with pytest.raises(adapter_mod.EngineMemoryError, match="available memory"):
        adapter.load(["multilingual"])
    assert adapter.loaded() == ["english"]  # nothing new was built

    # The floor counts only checkpoints that are not resident: already-loaded names pass.
    adapter.load(["english"])

    monkeypatch.setattr(
        adapter_mod, "_mem_available", lambda: int(adapter_mod.GB_PER_CHECKPOINT * 3)
    )
    adapter.load(["multilingual"])
    assert adapter.loaded() == ["english", "multilingual"]


def test_load_rejects_unknown_models_naming_them() -> None:
    adapter = make_adapter(StubEngine())
    with pytest.raises(ValueError, match="nope"):
        adapter.load(["nope"])


def test_loaded_and_device_reflect_the_router_without_constructing_it() -> None:
    engine = StubEngine()
    adapter = make_adapter(engine)
    assert adapter.loaded() == []
    assert engine.ctor_calls == []  # healthz probes must never build the engine
    assert adapter.device() == "cpu"
    adapter.predict(STATE, QUESTIONS)
    assert adapter.loaded() == ["english"]
    assert adapter.device() == "cpu"


def test_auto_device_is_legacy_unset_and_defaults_to_auto() -> None:
    engine = StubEngine()
    adapter = make_adapter(engine, device="auto")
    adapter.predict(STATE, QUESTIONS)
    assert engine.ctor_calls == [{"device": None, "preload": False}]
    assert adapter.device() == "auto"


def test_unload_is_a_noop_for_names_that_are_not_loaded() -> None:
    engine = StubEngine()
    adapter = warm(engine)
    adapter.unload(["multilingual", "english"])  # multilingual was never resident
    assert adapter.loaded() == []
    assert engine.built == ["english"]


def test_predict_model_override_flows_into_result_and_trace() -> None:
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans)
    ctx = start_trace(spans)
    result = adapter.predict(STATE, QUESTIONS, model="multilingual")
    trace = ctx.finish(200)
    assert trace is not None

    assert result["model"] == "multilingual"
    assert result["route_reason"] == "explicit model='multilingual'"
    assert trace.model == "multilingual"
    assert trace.route_reason == "explicit model='multilingual'"
    # Section 4.4 item 21: route.decide is absent by design when model= is explicit
    # (documented in docs/observability-model.md section 2). The decision still comes
    # from one untimed Router.route call, so the reason text is unchanged; lang was not
    # pinned, so detection still ran and recorded.
    names = [obs.name for obs in trace.observations]
    assert "route.decide" not in names
    assert "lang.detect" in names


def test_english_only_refusal_records_detection_but_no_forward() -> None:
    engine = StubEngine()
    spans = ThreadSpans()
    adapter = make_adapter(engine, spans=spans, english_only=True)
    warm_ctx = start_trace(spans)
    adapter.predict(STATE, QUESTIONS)  # construct + preload english; its load span stays here
    warm_ctx.finish(200)
    ctx = start_trace(spans)
    with pytest.raises(EnglishOnlyError) as excinfo:
        adapter.predict(STATE_NON_ENGLISH, QUESTIONS)
    trace = ctx.finish(422)
    assert trace is not None

    assert str(excinfo.value) == "english-only deployment: state did not detect as English"
    assert excinfo.value.detection == {
        "script": "Devanagari",
        "language": None,
        "non_latin_fraction": 1.0,
        "diacritic_rate": 0.0,
    }
    assert [obs.name for obs in trace.observations] == ["lang.detect"]
    assert trace.observations[0].meta == {"lang": "mul", "confidence": 0.5}
    assert trace.status == 422
    # Refusals keep model/route_reason/lang null (observability model section 3).
    assert trace.model is None
    assert trace.route_reason is None
    assert trace.lang is None
    assert "multilingual" not in adapter.loaded()  # the refusal never reached the engine
