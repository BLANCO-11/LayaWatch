"""Contract tests for the canonical span vocabulary (docs/observability-model.md section 2)."""
from __future__ import annotations

import pytest

from layawatch.obs.vocabulary import (
    SPAN_NAMES,
    SpanSpec,
    UnknownSpanError,
    observation_type,
    spec,
    validate,
)

# One fully valid attribute sample per entry, transcribed from the obs-model table.
VALID_ATTRS: dict[str, dict[str, object]] = {
    "http.receive": {
        "method": "POST",
        "path": "/predict",
        "remote": "127.0.0.1",
        "content_length": 512,
    },
    "auth.verify": {"scheme": "api_key", "key_id": "k-17", "ok": True},
    "body.parse": {
        "state_bytes": 4096,
        "question_count": 3,
        "model_param": "english",
        "lang_param": "en",
    },
    "lang.detect": {"lang": "en", "confidence": 0.97},
    "route.decide": {"model": "english", "reason": "keyword match", "typed_workflow": True},
    "queue.wait": {"depth_at_acquire": 2},
    "model.load": {"model": "english", "from": "disk", "bytes": 118_000_000},
    "forward": {"model": "english", "questions": 3, "temperature": 0.0, "device": "cpu"},
    "serialize": {"answers": 3, "bytes": 812},
    "response.send": {"status": 200, "bytes": 812},
    "error": {
        "code": "malformed_json",
        "message": "expecting value line 1 column 1",
        "where": "body.parse",
    },
}


def test_every_doc_table_name_is_in_span_names() -> None:
    assert set(VALID_ATTRS) == set(SPAN_NAMES)
    assert len(SPAN_NAMES) == 11


def test_every_name_validates_its_doc_attributes() -> None:
    for name, attrs in VALID_ATTRS.items():
        entry = spec(name)
        assert isinstance(entry, SpanSpec)
        assert entry.name == name
        assert observation_type(name) in {"span", "generation", "event"}
        assert validate(name, dict(attrs)) == attrs


def test_observation_types_follow_the_doc_table() -> None:
    assert observation_type("forward") == "generation"
    assert observation_type("error") == "event"
    span_names = SPAN_NAMES - {"forward", "error"}
    assert all(observation_type(name) == "span" for name in span_names)


def test_status_flag_marks_fallible_work_vs_failure_marker() -> None:
    assert spec("http.receive").status is True
    assert spec("forward").status is True
    assert spec("error").status is False


def test_unknown_span_name_raises_unknown_span_error() -> None:
    with pytest.raises(UnknownSpanError, match="teapot"):
        spec("teapot")
    with pytest.raises(UnknownSpanError, match="teapot"):
        validate("teapot", {})
    # UnknownSpanError stays a ValueError so broad handlers still catch vocabulary drift.
    assert issubclass(UnknownSpanError, ValueError)


def test_unknown_attribute_raises_and_names_the_key() -> None:
    with pytest.raises(ValueError, match="verb"):
        validate("http.receive", {"method": "POST", "verb": "POST"})


@pytest.mark.parametrize(
    ("name", "attrs"),
    [
        ("http.receive", {"method": 123}),
        ("http.receive", {"content_length": "512"}),
        ("http.receive", {"content_length": True}),
        ("auth.verify", {"ok": "yes"}),
        ("lang.detect", {"confidence": "high"}),
        ("route.decide", {"typed_workflow": 1}),
        ("forward", {"temperature": "0.0"}),
        ("response.send", {"status": 200.0}),
        ("model.load", {"from": 3}),
    ],
)
def test_wrong_attribute_type_raises(name: str, attrs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="expected"):
        validate(name, attrs)


def test_int_attributes_coerce_to_float_but_not_bool() -> None:
    assert validate("lang.detect", {"confidence": 1}) == {"confidence": 1.0}
    assert validate("forward", {"temperature": 0}) == {"temperature": 0.0}
    assert isinstance(validate("lang.detect", {"confidence": 1})["confidence"], float)


def test_missing_attributes_are_allowed_but_types_still_hold() -> None:
    assert validate("route.decide", {}) == {}
    assert validate("body.parse", {"question_count": 2}) == {"question_count": 2}
    assert validate("auth.verify", {"scheme": "none", "ok": False}) == {
        "scheme": "none",
        "ok": False,
    }
