"""Recorder contract tests: spans, totals, sampling, ring, sinks, inert fast path."""
from __future__ import annotations

import random
import re
import time

import pytest

from layawatch.obs.recorder import Recorder, current_trace_id
from layawatch.obs.redact import byte_len


def test_span_ordering_offsets_and_nesting() -> None:
    rec = Recorder()
    ctx = rec.start("/predict", "POST", "aabbccdd", client_ip="10.0.0.1")
    with ctx.span(
        "http.receive",
        method="POST",
        path="/predict",
        remote="10.0.0.1",
        content_length=64,
    ):
        time.sleep(0.012)
        with ctx.span("auth.verify", scheme="api_key", key_id="k1", ok=True):
            time.sleep(0.012)
        with ctx.span("lang.detect", lang="en", confidence=0.9):
            time.sleep(0.005)
    with ctx.span("response.send", status=200, bytes=10):
        time.sleep(0.005)
    trace = ctx.finish(200)
    assert trace is not None
    assert trace.id == "aabbccdd"
    assert [o.name for o in trace.observations] == [
        "http.receive",
        "auth.verify",
        "lang.detect",
        "response.send",
    ]
    recv, auth, lang, send = trace.observations
    # start_ms is an offset from trace start and never decreases.
    assert 0.0 <= recv.start_ms < auth.start_ms
    assert auth.start_ms <= lang.start_ms < send.start_ms
    # Offsets are consistent with nesting: a child starts inside its parent's window.
    assert recv.parent_id is None
    assert send.parent_id is None
    assert auth.parent_id == recv.id
    assert lang.parent_id == recv.id
    # Durations track the sleeps; the parent covers both children.
    assert auth.duration_ms >= 12.0
    assert lang.duration_ms >= 5.0
    assert recv.duration_ms >= 29.0
    assert send.duration_ms >= 5.0
    assert trace.duration_ms >= recv.duration_ms + send.duration_ms
    # Attribute schemas landed in observation meta.
    assert recv.meta == {
        "method": "POST",
        "path": "/predict",
        "remote": "10.0.0.1",
        "content_length": 64,
    }


def test_duration_and_framework_ms_with_nested_and_top_level_spans() -> None:
    rec = Recorder()
    ctx = rec.start("/route", "GET")
    with ctx.span(
        "http.receive", method="GET", path="/route", remote="127.0.0.1", content_length=0
    ):
        time.sleep(0.012)
        with ctx.span("forward", model="english", questions=1, temperature=0.0, device="cpu"):
            time.sleep(0.018)
    time.sleep(0.005)  # framework gap between the two top-level spans
    with ctx.span("response.send", status=200, bytes=4):
        time.sleep(0.006)
    trace = ctx.finish(200)
    assert trace is not None
    recv, gen, send = trace.observations
    assert gen.type == "generation"
    assert gen.parent_id == recv.id
    top_level = [o for o in trace.observations if o.parent_id is None]
    assert [o.name for o in top_level] == ["http.receive", "response.send"]
    top_sum = sum(o.duration_ms for o in top_level)
    # framework_ms is exactly duration minus the top-level sum, and >= 0 by construction.
    framework = trace.meta["framework_ms"]
    assert framework == pytest.approx(trace.duration_ms - top_sum, abs=1e-6)
    assert framework >= 0.0
    # The 5 ms inter-span gap is framework time, so the overhead is at least that.
    assert framework >= 4.0
    assert trace.duration_ms >= 40.0  # ~12 + 18 + 5 + 6 ms of sleeps (1 ms slack)
    assert recv.duration_ms >= 30.0  # nested generation time belongs to the parent span


def test_exception_marks_span_error_and_reraises() -> None:
    rec = Recorder()
    ctx = rec.start("/predict", "POST", "badbad01")
    with pytest.raises(ValueError, match="boom"):
        with ctx.span(
            "body.parse",
            state_bytes=10,
            question_count=1,
            model_param="english",
            lang_param="en",
        ):
            time.sleep(0.003)
            raise ValueError("boom")
    trace = ctx.finish(400)
    assert trace is not None
    assert trace.status == 400
    assert len(trace.observations) == 1
    obs = trace.observations[0]
    assert obs.name == "body.parse"
    assert obs.status == "error"
    assert obs.duration_ms >= 3.0
    assert obs.parent_id is None  # the escaped exception still popped the span off the stack


def test_event_records_instantaneously_under_the_enclosing_span() -> None:
    rec = Recorder()
    ctx = rec.start("/predict", "POST")
    with ctx.span("http.receive", method="POST", path="/predict", remote="r", content_length=0):
        err = ctx.event("error", code="malformed_json", message="bad body", where="body.parse")
    trace = ctx.finish(400)
    assert trace is not None
    recv, event = trace.observations
    assert err.id == event.id
    assert event.type == "event"
    assert event.status == "error"  # the error event is a failure marker, not fallible work
    assert event.duration_ms == 0.0
    assert event.parent_id == recv.id
    assert event.meta == {
        "code": "malformed_json",
        "message": "bad body",
        "where": "body.parse",
    }


def test_span_and_event_names_are_cross_checked_loudly() -> None:
    rec = Recorder()
    ctx = rec.start("/route", "GET")
    with pytest.raises(ValueError, match="Context.event"):
        ctx.span("error", code="x", message="y", where="z")
    with pytest.raises(ValueError, match="Context.span"):
        ctx.event("forward", model="m", questions=1, temperature=0.0, device="cpu")
    ctx.finish(200)


def test_set_fields_land_on_the_trace() -> None:
    rec = Recorder()
    ctx = rec.start("/route", "GET", "setfield")
    ctx.set(
        model="english",
        route_reason="keyword",
        lang="en",
        queue_ms=5.5,
        forward_ms=12.25,
        state_bytes=4096,
        question_count=3,
        client_key_id="k-9",
        session_id="sess-1",
        tags=["playground"],
        meta={"device": "cpu"},
    )
    trace = ctx.finish(200)
    assert trace is not None
    assert trace.model == "english"
    assert trace.route_reason == "keyword"
    assert trace.lang == "en"
    assert trace.queue_ms == 5.5
    assert trace.forward_ms == 12.25
    assert trace.state_bytes == 4096
    assert trace.question_count == 3
    assert trace.client_key_id == "k-9"
    assert trace.session_id == "sess-1"
    assert trace.tags == ["playground"]
    assert trace.meta["device"] == "cpu"
    assert trace.route == "/route" and trace.method == "GET"


def test_set_rejects_unknown_and_recorder_owned_fields_and_truncates_error_message() -> None:
    rec = Recorder()
    ctx = rec.start("/route", "GET")
    with pytest.raises(ValueError, match="unknown trace field"):
        ctx.set(nope=1)
    with pytest.raises(ValueError, match="owned by the recorder"):
        ctx.set(status=500)
    # docs trace-attribute table: error_message truncated to 500 chars.
    ctx.set(error_code="E_TOO_LONG", error_message="x" * 900)
    trace = ctx.finish(500)
    assert trace is not None
    assert trace.error_code == "E_TOO_LONG"
    assert trace.error_message == "x" * 500


def test_sampling_rate_zero_keeps_errors_and_delivers_success_without_detail() -> None:
    delivered: list = []
    logged: list = []
    rec = Recorder(sample_rate=0.0, on_trace=delivered.append, on_log=logged.append)

    ctx = rec.start("/predict", "POST")
    refused = ctx.finish(422)
    assert refused is not None  # refusals are never sampled away
    assert refused.meta["sampled"] is True

    ctx = rec.start("/predict", "POST")
    ctx.set(error_code="E_MODEL_MISSING")
    coded = ctx.finish(200)
    assert coded is not None  # an error_code forces a keep even at rate 0
    assert coded.meta["sampled"] is True

    # A plain success is sampled out but not silenced: it is still delivered to on_trace
    # so rollups stay accurate, just without detail.
    ctx = rec.start("/predict", "POST")
    with ctx.span(
        "http.receive", method="POST", path="/predict", remote="r", content_length=0
    ):
        time.sleep(0.003)
    dropped_id = ctx.trace_id
    dropped = ctx.finish(200)
    assert dropped is not None  # finish still returns the trace
    assert dropped.meta["sampled"] is False
    assert dropped.observations == []  # detail is skipped
    assert dropped.status == 200 and dropped.duration_ms >= 0.0  # rollup columns survive

    # The ring holds only kept traces; on_trace fired for every delivered trace.
    assert rec.ring_traces() == [refused, coded]
    assert [t.id for t in delivered] == [refused.id, coded.id, dropped_id]
    assert len(logged) == 1
    assert logged[0]["trace_id"] == dropped_id
    assert logged[0]["level"] == "debug"
    assert "sampled out" in logged[0]["message"]


def test_playground_runs_are_always_recorded() -> None:
    delivered: list = []
    rec = Recorder(sample_rate=0.0, on_trace=delivered.append)

    # session_id marks a playground run: kept with full detail despite rate 0.
    ctx = rec.start("/predict", "POST", "play0001", session_id="sess-42")
    with ctx.span(
        "http.receive", method="POST", path="/predict", remote="r", content_length=0
    ):
        pass
    via_session = ctx.finish(200)
    assert via_session is not None
    assert via_session.meta["sampled"] is True
    assert len(via_session.observations) == 1  # detail kept, unlike a sampled-out success

    # meta.source == "playground" alone is the same signal.
    ctx = rec.start("/predict", "POST", "play0002")
    ctx.set(meta={"source": "playground"})
    via_meta = ctx.finish(200)
    assert via_meta is not None
    assert via_meta.meta["sampled"] is True

    assert rec.ring_traces() == [via_session, via_meta]
    assert delivered == [via_session, via_meta]


def test_sampling_rate_one_keeps_every_trace() -> None:
    delivered: list = []
    rec = Recorder(sample_rate=1.0, on_trace=delivered.append, ring_size=100)
    for _ in range(10):
        ctx = rec.start("/route", "GET")
        trace = ctx.finish(200)
        assert trace is not None
        assert trace.meta["sampled"] is True
    assert len(delivered) == 10
    assert len(rec.ring_traces()) == 10  # at rate 1 every delivery is also kept


def test_sampling_is_deterministic_with_a_seeded_rng() -> None:
    def keep_sequence(seed: int) -> list[bool]:
        rec = Recorder(sample_rate=0.5, rng=random.Random(seed))
        decisions = []
        for _ in range(24):
            ctx = rec.start("/route", "GET")
            trace = ctx.finish(200)
            assert trace is not None
            decisions.append(trace.meta["sampled"])
        return decisions

    first = keep_sequence(7)
    assert keep_sequence(7) == first  # same seed, same decisions
    assert set(first) == {True, False}  # rate 0.5 over 24 traces actually samples
    assert keep_sequence(8) != first  # a different seed yields a different sequence


def test_ring_evicts_fifo_at_ring_size() -> None:
    rec = Recorder(ring_size=3)
    trace_ids = []
    for _ in range(5):
        ctx = rec.start("/route", "GET")
        trace = ctx.finish(200)
        assert trace is not None
        trace_ids.append(trace.id)
    # Oldest two evicted; snapshot is oldest-to-newest.
    assert [t.id for t in rec.ring_traces()] == trace_ids[2:]
    assert [t.id for t in rec.ring_traces(2)] == trace_ids[3:]
    assert rec.ring_traces(0) == []
    assert len(rec.ring_traces(None)) == 3
    assert len(rec.ring_traces()) == 3


def test_disabled_recorder_returns_inert_context() -> None:
    sink_calls: list = []
    log_calls: list = []
    rec = Recorder(
        enabled=False, capture=True, on_trace=sink_calls.append, on_log=log_calls.append
    )
    ctx = rec.start("/predict", "POST", "inert0001")
    # The fast path does no timing, validation, capture or dict work.
    with ctx.span("not.a.real.span", anything=object()):
        pass
    assert ctx.event("error", code="x", message="y", where="z") is None
    ctx.set(model="english")
    assert current_trace_id() is None  # no thread-local was bound
    assert ctx.finish(422) is None  # even an error status records nothing
    assert rec.ring_traces() == []
    assert sink_calls == []
    assert log_calls == []


def test_current_trace_id_resolves_inside_context_and_none_outside() -> None:
    rec = Recorder()
    assert current_trace_id() is None
    provider = rec.trace_provider()
    assert provider() is None
    ctx = rec.start("/predict", "POST", "1cebeef5")
    assert current_trace_id() == "1cebeef5"
    assert provider() == "1cebeef5"
    with ctx.span(
        "http.receive", method="POST", path="/predict", remote="r", content_length=0
    ):
        assert current_trace_id() == "1cebeef5"
    assert ctx.finish(200) is not None
    assert current_trace_id() is None
    assert provider() is None


def test_generated_trace_ids_are_unique_8_hex() -> None:
    rec = Recorder(ring_size=64)
    ids = []
    for _ in range(50):
        ctx = rec.start("/route", "GET")
        trace = ctx.finish(200)
        assert trace is not None
        ids.append(trace.id)
    assert len(set(ids)) == 50
    assert all(re.fullmatch(r"[0-9a-f]{8}", trace_id) for trace_id in ids)


def test_capture_false_never_writes_payload_content() -> None:
    def record(capture: bool, payload_max: int = 2048):
        rec = Recorder(capture=capture, payload_max=payload_max)
        ctx = rec.start("/predict", "POST")
        with ctx.span(
            "forward",
            input={"state": {"body": "Confidential letter body"}},
            output={"answers": {"q1": {"choice": "refund"}}},
            model="english",
            questions=1,
            temperature=0.0,
            device="cpu",
        ):
            time.sleep(0.003)
        trace = ctx.finish(200)
        assert trace is not None
        return trace

    off = record(capture=False)
    gen_off = off.observations[0]
    assert gen_off.input is None
    assert gen_off.output is None
    assert off.meta["payload_capture"] is False
    # Non-payload attribute meta is always recorded.
    assert gen_off.meta is not None and gen_off.meta["model"] == "english"

    on = record(capture=True)
    gen_on = on.observations[0]
    assert gen_on.input is not None
    assert "Confidential letter body" in gen_on.input
    assert gen_on.output is not None and "refund" in gen_on.output
    assert on.meta["payload_capture"] is True

    small = record(capture=True, payload_max=16)
    gen_small = small.observations[0]
    assert gen_small.input is not None
    assert byte_len(gen_small.input) <= 16


def test_finish_is_single_shot_and_spans_after_finish_raise() -> None:
    rec = Recorder()
    ctx = rec.start("/route", "GET")
    assert ctx.finish(200) is not None
    assert ctx.finish(500) is None  # double close records nothing
    assert len(rec.ring_traces()) == 1
    with pytest.raises(RuntimeError, match="already finished"):
        ctx.span("queue.wait", depth_at_acquire=0)
    with pytest.raises(RuntimeError, match="already finished"):
        ctx.set(model="english")


def test_constructor_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="ring_size"):
        Recorder(ring_size=0)
    with pytest.raises(ValueError, match="sample_rate"):
        Recorder(sample_rate=1.5)
    with pytest.raises(ValueError, match="sample_rate"):
        Recorder(sample_rate=-0.1)
    with pytest.raises(ValueError, match="payload_max"):
        Recorder(payload_max=-1)
