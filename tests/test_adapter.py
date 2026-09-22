"""Contract tests for the EngineAdapter protocol and the deterministic FakeAdapter."""
from __future__ import annotations

import threading
import time

import pytest

from layawatch.engine.adapter import EngineAdapter, FakeAdapter

STATE = {
    "from": "user@acme.com",
    "subject": "Duplicate charge on invoice #4411",
    "body": "Hi, we were billed twice for March. Please refund the duplicate today "
            "or we will cancel our plan.",
}
# Non-ASCII source text via escapes: keeps this file ASCII while still detecting as non-English.
STATE_NON_ENGLISH = {"body": "\u092e\u0941\u091c\u0932\u0938\u0947 \u091a\u093e\u0930\u091c \u0935\u093e\u092a\u0938"}

QUESTIONS = {
    "department": {
        "type": "choice",
        "instructions": "Which department should handle this request?",
        "criteria": {
            "billing": "invoices, payments, refunds",
            "technical": "bugs, outages, system errors",
            "sales": "pricing, new contracts",
            "other": "everything else",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this request?",
        "criteria": ["not urgent", "soon", "critical deadline or blocking issue"],
    },
    "churn_risk": {
        "type": "noul",
        "instructions": "Does the user threaten to cancel or leave?",
    },
}


def test_protocol_runtime_checkable_accepts_fake_adapter() -> None:
    assert isinstance(FakeAdapter(), EngineAdapter)
    assert not isinstance(object(), EngineAdapter)


def test_predict_returns_documented_shape_and_ranges() -> None:
    result = FakeAdapter().predict(STATE, QUESTIONS)
    assert set(result) == {"answers", "model", "route_reason", "lang"}
    assert isinstance(result["model"], str) and result["model"]
    assert isinstance(result["route_reason"], str) and result["route_reason"]
    assert isinstance(result["lang"], str) and result["lang"]

    answers = result["answers"]
    assert set(answers) == set(QUESTIONS)
    for answer in answers.values():
        assert set(answer) == {"choice", "confidence", "noul"}
        assert isinstance(answer["confidence"], float)
        assert 0.0 <= answer["confidence"] <= 1.0
        assert isinstance(answer["noul"], float)
        assert 0.0 <= answer["noul"] <= 1.0
    assert answers["department"]["choice"] in QUESTIONS["department"]["criteria"]
    assert answers["urgency"]["choice"] in QUESTIONS["urgency"]["criteria"]
    assert answers["churn_risk"]["choice"] is None


def test_route_returns_decision_without_answers() -> None:
    result = FakeAdapter().route(STATE, QUESTIONS)
    assert set(result) == {"model", "reason", "lang"}
    assert isinstance(result["model"], str) and result["model"]
    assert isinstance(result["reason"], str) and result["reason"]
    assert isinstance(result["lang"], str) and result["lang"]


def test_route_and_predict_agree_on_the_decision() -> None:
    adapter = FakeAdapter(models=("english", "multilingual"))
    predicted = adapter.predict(STATE, QUESTIONS)
    routed = adapter.route(STATE, QUESTIONS)
    assert predicted["model"] == routed["model"]
    assert predicted["route_reason"] == routed["reason"]
    assert predicted["lang"] == routed["lang"]


def test_predict_is_deterministic_across_calls_and_instances() -> None:
    adapter = FakeAdapter()
    first = adapter.predict(STATE, QUESTIONS)
    second = adapter.predict(STATE, QUESTIONS)
    assert first == second
    assert FakeAdapter().predict(STATE, QUESTIONS) == first


def test_predict_is_deterministic_under_concurrent_calls() -> None:
    threads_count = 4
    adapter = FakeAdapter()
    baseline = adapter.predict(STATE, QUESTIONS)
    results: list[dict | None] = [None] * threads_count
    barrier = threading.Barrier(threads_count)

    def call(index: int) -> None:
        barrier.wait()
        results[index] = adapter.predict(STATE, QUESTIONS)

    workers = [threading.Thread(target=call, args=(i,)) for i in range(threads_count)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert results == [baseline] * threads_count


def test_latency_is_applied_then_call_completes() -> None:
    adapter = FakeAdapter(latency_ms=5)

    start = time.perf_counter()
    predicted = adapter.predict(STATE, QUESTIONS)
    predict_ms = (time.perf_counter() - start) * 1000
    assert predict_ms >= 5
    assert set(predicted) == {"answers", "model", "route_reason", "lang"}

    start = time.perf_counter()
    routed = adapter.route(STATE, QUESTIONS)
    route_ms = (time.perf_counter() - start) * 1000
    assert route_ms >= 5
    assert set(routed) == {"model", "reason", "lang"}


def test_error_injection_fails_then_recovers() -> None:
    adapter = FakeAdapter(error=RuntimeError("engine exploded"), fail_times=2)
    with pytest.raises(RuntimeError, match="engine exploded"):
        adapter.predict(STATE, QUESTIONS)
    with pytest.raises(RuntimeError, match="engine exploded"):
        adapter.route(STATE, QUESTIONS)
    # Budget shared across predict and route: both calls consumed it, now both recover.
    predicted = adapter.predict(STATE, QUESTIONS)
    assert set(predicted) == {"answers", "model", "route_reason", "lang"}
    assert set(adapter.route(STATE, QUESTIONS)) == {"model", "reason", "lang"}
    assert adapter.counters()["predict"] == 2
    assert adapter.counters()["route"] == 2


def test_negative_fail_times_fails_forever() -> None:
    adapter = FakeAdapter(error=RuntimeError("permanent failure"), fail_times=-1)
    for _ in range(3):
        with pytest.raises(RuntimeError, match="permanent failure"):
            adapter.predict(STATE, QUESTIONS)
    with pytest.raises(RuntimeError, match="permanent failure"):
        adapter.route(STATE, QUESTIONS)


def test_fail_times_requires_an_error() -> None:
    with pytest.raises(ValueError, match="error"):
        FakeAdapter(fail_times=1)


def test_model_lifecycle_invariants() -> None:
    adapter = FakeAdapter(models=("english", "multilingual"))
    assert adapter.loaded() == []

    adapter.load(["multilingual", "english"])
    assert adapter.loaded() == ["english", "multilingual"]  # sorted, not insertion order
    adapter.load(["english"])  # idempotent
    assert adapter.loaded() == ["english", "multilingual"]

    with pytest.raises(ValueError, match="bert"):
        adapter.load(["bert"])
    assert adapter.loaded() == ["english", "multilingual"]  # rejected load changed nothing

    adapter.unload(["english"])
    assert adapter.loaded() == ["multilingual"]
    adapter.unload(["english", "bert"])  # unloaded and unknown names are a no-op
    assert adapter.loaded() == ["multilingual"]
    assert adapter.device() == "fake"


def test_initially_loaded_seeds_the_model_set() -> None:
    assert FakeAdapter().loaded() == []
    adapter = FakeAdapter(models=("english", "multilingual"), initially_loaded=("multilingual",))
    assert adapter.loaded() == ["multilingual"]
    with pytest.raises(ValueError, match="bert"):
        FakeAdapter(initially_loaded=("bert",))


def test_counters_report_call_counts_per_method() -> None:
    adapter = FakeAdapter()
    assert adapter.counters() == {
        "predict": 0, "route": 0, "loaded": 0, "load": 0, "unload": 0, "device": 0,
    }
    adapter.predict(STATE, QUESTIONS)
    adapter.route(STATE, QUESTIONS)
    adapter.loaded()
    adapter.load(["english"])
    adapter.unload(["english"])
    adapter.device()
    assert adapter.counters() == {
        "predict": 1, "route": 1, "loaded": 1, "load": 1, "unload": 1, "device": 1,
    }


def test_decision_is_derived_from_the_state_language() -> None:
    adapter = FakeAdapter(models=("english", "multilingual"))
    english = adapter.route(STATE, QUESTIONS)
    non_english = adapter.route(STATE_NON_ENGLISH, QUESTIONS)
    assert english == {"model": "english", "reason": "state is English", "lang": "en"}
    assert non_english == {
        "model": "multilingual",
        "reason": "state is not English",
        "lang": "mul",
    }


def test_predict_model_override_is_echoed() -> None:
    adapter = FakeAdapter(models=("english", "multilingual"))
    result = adapter.predict(STATE, QUESTIONS, model="multilingual", task="triage")
    assert result["model"] == "multilingual"
    assert set(result) == {"answers", "model", "route_reason", "lang"}
