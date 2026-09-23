"""Engine route contract: registration, shape validation, RealAdapter span flow, concurrency."""
from __future__ import annotations

import json
import threading
import time

from layawatch.api.engine import add_engine_routes
from layawatch.engine.adapter import RealAdapter
from layawatch.http.middleware import ThreadLocalSpans, instrument
from layawatch.http.router import Router
from layawatch.http.types import Request
from layawatch.obs.recorder import Recorder, Trace
from layawatch.obs.vocabulary import SPAN_NAMES, validate
from layawatch.store.db import connect, migrate

BODY = {"state": {"dept": "billing"}, "questions": {"churn": {"choice": "yes"}}}
BODY_BYTES = len(json.dumps(BODY).encode("utf-8"))


class _StubLang:
    def __init__(self, is_english: bool) -> None:
        self._is_english = is_english

    def analyse(self, _state) -> dict:
        return {"is_english": self._is_english, "language": "en" if self._is_english else "ru"}


class _StubAgent:
    """Fake ``system_one``; ``block=True`` holds the forward pass until the test releases it."""

    def __init__(self, block: bool) -> None:
        self.block = block
        self.in_flight = threading.Event()
        self.release = threading.Event()

    def system_one(self, _state, questions) -> dict:
        if self.block:
            self.in_flight.set()
            assert self.release.wait(timeout=5), "test never released the forward lock"
        answers = {
            name: {"choice": "yes", "confidence": 0.9, "noul": 0.1} for name in questions
        }
        return {"answers": answers}


class _StubRouter:
    def __init__(self, device: str, agent: _StubAgent) -> None:
        self.device = device
        self.loaded = ["english", "multilingual"]
        self.models = {"english": "stub/english", "multilingual": "stub/multilingual"}
        self._agent = agent

    def preload(self, _models) -> None:
        pass

    def route(self, _state, _questions, *, model=None, task=None) -> dict:
        if model is not None and model not in self.loaded:
            raise ValueError(f"unknown model: {model}")
        return {"model": model or "english", "reason": "state is English", "workflow": None}

    def load(self, _name) -> _StubAgent:
        return self._agent


class _StubEngine:
    """The ``laya`` module surface RealAdapter touches: ``Router`` class and ``lang.analyse``."""

    def __init__(self, agent: _StubAgent, is_english: bool) -> None:
        self.lang = _StubLang(is_english)
        self._agent = agent

    def Router(self, *, device=None, preload=False) -> _StubRouter:  # noqa: N802 - mirrors laya.Router
        return _StubRouter(device or "cpu", self._agent)


def build(tmp_path, *, english_only=False, is_english=True, block=False, capture=False):
    db_path = tmp_path / "state.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    recorder = Recorder(capture=capture, payload_max=32768)
    agent = _StubAgent(block=block)
    spans = ThreadLocalSpans(recorder)
    adapter = RealAdapter(
        engine=_StubEngine(agent, is_english),
        spans=spans,
        device="cpu",
        models=("english", "multilingual"),
        english_only=english_only,
        state_dir=tmp_path,
    )
    router = Router()
    add_engine_routes(router, adapter)
    handle = instrument(router, recorder, db_path, spans=spans)
    return handle, recorder, router, adapter, agent


def make_request(
    method: str = "POST",
    target: str = "/predict",
    body: dict | bytes | None = None,
    request_id: str = "a1b2c3d4",
) -> Request:
    raw = body if isinstance(body, bytes) else json.dumps(BODY if body is None else body)
    if not isinstance(raw, bytes):
        raw = raw.encode("utf-8")
    return Request.build(method, target, {}, raw, request_id, "127.0.0.1")


def observations(trace: Trace, name: str):
    return [obs for obs in trace.observations if obs.name == name]


def assert_vocabulary(trace: Trace) -> None:
    for obs in trace.observations:
        assert obs.name in SPAN_NAMES, obs.name
        validate(obs.name, obs.meta or {})


def test_engine_routes_accept_post_only(tmp_path) -> None:
    _handle, recorder, router, _adapter, _agent = build(tmp_path)
    response = router.dispatch(make_request("GET", "/predict", body=b""))
    assert response.status == 405
    assert json.loads(response.body)["error"]["code"] == "method_not_allowed"
    allowed = {name.strip() for name in response.headers["Allow"].split(",")}
    assert allowed == {"POST", "OPTIONS"}
    assert recorder.ring_traces() == []  # plain dispatch, no middleware: nothing recorded


def test_predict_with_real_adapter_records_full_nine_span_sequence(tmp_path) -> None:
    handle, recorder, _router, _adapter, _agent = build(tmp_path)
    response = handle(make_request())

    assert response.status == 200
    trace = recorder.ring_traces()[-1]
    names = [obs.name for obs in trace.observations]
    print(f"nine-span order: {names}")
    assert names == [
        "http.receive",
        "auth.verify",
        "body.parse",
        "lang.detect",
        "route.decide",
        "queue.wait",
        "forward",
        "serialize",
        "response.send",
    ]
    assert "model.load" not in names  # stub checkpoint is warm: exactly nine
    assert_vocabulary(trace)

    assert observations(trace, "lang.detect")[0].meta == {"lang": "en", "confidence": 1.0}
    assert observations(trace, "route.decide")[0].meta == {
        "model": "english",
        "reason": "state is English",
        "typed_workflow": False,
    }
    assert observations(trace, "queue.wait")[0].meta == {"depth_at_acquire": 0}
    assert observations(trace, "forward")[0].meta == {
        "model": "english",
        "questions": 1,
        "device": "cpu",
    }
    assert observations(trace, "serialize")[0].meta == {"answers": 1}
    parsed = observations(trace, "body.parse")[0]
    assert parsed.meta == {"state_bytes": BODY_BYTES, "question_count": 1}

    assert trace.status == 200
    assert trace.route == "/predict"
    assert trace.model == "english"
    assert trace.route_reason == "state is English"
    assert trace.lang == "en"
    assert trace.question_count == 1
    assert trace.state_bytes == BODY_BYTES
    assert trace.queue_ms >= 0
    assert trace.forward_ms is not None and trace.forward_ms >= 0
    assert response.headers["X-Request-Id"] == trace.id
    payload = json.loads(response.body)
    assert payload["model"] == "english"
    assert payload["usage"] is None


def test_route_returns_decision_without_forward_spans(tmp_path) -> None:
    handle, recorder, _router, _adapter, _agent = build(tmp_path)
    response = handle(make_request(target="/route"))

    assert response.status == 200
    assert json.loads(response.body) == {
        "model": "english",
        "reason": "state is English",
        "lang": "en",
    }
    trace = recorder.ring_traces()[-1]
    names = [obs.name for obs in trace.observations]
    assert names == [
        "http.receive",
        "auth.verify",
        "body.parse",
        "lang.detect",
        "route.decide",
        "response.send",
    ]
    assert trace.route == "/route"
    assert trace.model == "english"
    assert trace.route_reason == "state is English"
    assert trace.lang == "en"
    assert trace.question_count == 1
    assert_vocabulary(trace)


def test_missing_questions_is_400_invalid_request_with_field(tmp_path) -> None:
    handle, recorder, _router, _adapter, _agent = build(tmp_path)
    response = handle(make_request(body={"state": {"dept": "billing"}}))

    assert response.status == 400
    error = json.loads(response.body)["error"]
    assert error["code"] == "invalid_request"
    assert error["details"] == {"field": "questions"}
    trace = recorder.ring_traces()[-1]
    assert trace.status == 400
    assert trace.error_code == "invalid_request"
    assert observations(trace, "error")[0].meta["where"] == "handler"


def test_non_dict_state_is_400_invalid_request(tmp_path) -> None:
    handle, _recorder, _router, _adapter, _agent = build(tmp_path)
    response = handle(make_request(body={"state": "plain text", "questions": {"q": {}}}))

    assert response.status == 400
    error = json.loads(response.body)["error"]
    assert error["code"] == "invalid_request"
    assert error["details"] == {"field": "state"}


def test_non_object_body_is_400_invalid_request(tmp_path) -> None:
    handle, _recorder, _router, _adapter, _agent = build(tmp_path)
    response = handle(make_request(body=b'[1, 2]'))

    assert response.status == 400
    error = json.loads(response.body)["error"]
    assert error["code"] == "invalid_request"
    assert error["details"] == {"field": "body"}


def test_engine_value_error_is_400_with_engine_message(tmp_path) -> None:
    handle, recorder, _router, _adapter, _agent = build(tmp_path)
    body = dict(BODY, model="nope")
    response = handle(make_request(body=body))

    assert response.status == 400
    error = json.loads(response.body)["error"]
    assert error["code"] == "invalid_request"
    assert error["message"] == "unknown model: nope"
    trace = recorder.ring_traces()[-1]
    assert trace.status == 400
    names = [obs.name for obs in trace.observations]
    assert "lang.detect" in names  # detection ran before routing refused the override
    assert "route.decide" not in names
    assert "forward" not in names


def test_english_only_refusal_is_422_after_lang_detect_without_forward(tmp_path) -> None:
    handle, recorder, _router, _adapter, _agent = build(tmp_path, english_only=True, is_english=False)
    cyrillic = {"state": {"dept": "счета"}, "questions": {"q": {}}}
    response = handle(make_request(body=cyrillic))

    assert response.status == 422
    error = json.loads(response.body)["error"]
    assert error["code"] == "english_only"
    assert set(error["details"]["detection"]) == {
        "script",
        "language",
        "non_latin_fraction",
        "diacritic_rate",
    }
    trace = recorder.ring_traces()[-1]
    names = [obs.name for obs in trace.observations]
    assert names == [
        "http.receive",
        "auth.verify",
        "body.parse",
        "lang.detect",
        "error",
        "response.send",
    ]
    assert "forward" not in names
    assert trace.status == 422
    assert trace.error_code == "english_only"
    assert observations(trace, "error")[0].status == "error"
    assert_vocabulary(trace)


def test_concurrent_predicts_queue_behind_the_forward_lock(tmp_path) -> None:
    handle, recorder, _router, adapter, agent = build(tmp_path, block=True)
    results: dict[str, int] = {}

    def predict(tag: str) -> None:
        results[tag] = handle(make_request()).status

    first = threading.Thread(target=predict, args=("first",))
    first.start()
    assert agent.in_flight.wait(timeout=5), "first predict never reached the forward pass"

    second = threading.Thread(target=predict, args=("second",))
    second.start()
    deadline = time.time() + 5
    while adapter._inflight < 2 and time.time() < deadline:  # second caller past routing
        time.sleep(0.001)
    assert adapter._inflight == 2, "second predict never started"
    time.sleep(0.05)  # now blocked inside queue.wait on the forward lock
    agent.release.set()
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()

    assert results == {"first": 200, "second": 200}
    traces = recorder.ring_traces()
    assert len(traces) == 2
    assert [trace.status for trace in traces] == [200, 200]

    queue_ms = [trace.queue_ms for trace in traces]
    assert max(queue_ms) > 0, queue_ms  # the second caller waited on the forward lock
    waits = [
        obs
        for trace in traces
        for obs in trace.observations
        if obs.name == "queue.wait"
    ]
    assert len(waits) == 2
    assert max(obs.duration_ms for obs in waits) > 0
    assert any(obs.meta.get("depth_at_acquire") == 1 for obs in waits)
    for trace in traces:
        assert_vocabulary(trace)


def test_predict_records_request_and_response_payload_when_capture_is_on(tmp_path) -> None:
    handle, recorder, _router, _adapter, _agent = build(tmp_path, capture=True)
    body = {
        "state": {"body": "refund jane@acme.example, call +44 20 7946 0958"},
        "questions": BODY["questions"],
    }
    response = handle(make_request(body=body))
    assert response.status == 200
    trace = recorder.ring_traces()[-1]
    assert_vocabulary(trace)
    (payload,) = observations(trace, "payload")
    captured_in = json.loads(payload.input)
    captured_out = json.loads(payload.output)
    assert captured_in["questions"] == BODY["questions"]
    assert captured_out["answers"] == json.loads(response.body)["answers"]
    # PII in free text is scrubbed from the stored copy.
    assert "jane@acme.example" not in payload.input and "7946" not in payload.input
    assert "[redacted:email]" in captured_in["state"]["body"]


def test_predict_records_the_request_even_when_it_is_refused(tmp_path) -> None:
    handle, recorder, _router, _adapter, _agent = build(
        tmp_path, capture=True, english_only=True, is_english=False
    )
    response = handle(make_request())
    assert response.status == 422
    (payload,) = observations(recorder.ring_traces()[-1], "payload")
    assert json.loads(payload.input)["questions"] == BODY["questions"]
    assert payload.output is None


def test_no_payload_event_while_capture_is_off(tmp_path) -> None:
    handle, recorder, _router, _adapter, _agent = build(tmp_path)
    assert handle(make_request()).status == 200
    assert observations(recorder.ring_traces()[-1], "payload") == []
