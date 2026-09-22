"""Canonical span vocabulary for LayaWatch traces.

Names, attribute schemas and observation types follow docs/observability-model.md section 2
verbatim. ``validate`` is the loud gate between the request path and the store: unknown span
names raise ``UnknownSpanError``, unknown attributes and mistyped values raise ``ValueError``
with the offending key named, so malformed observations never reach SQLite.

``SpanSpec.status`` distinguishes timed fallible work from failure-marker events:

- ``True`` (span and generation entries): the observation starts with status ``ok`` and flips
  to ``error`` when the recording context manager sees an exception escape.
- ``False`` (the ``error`` event): the observation is itself the failure marker and is
  recorded with status ``error`` immediately.
"""
from __future__ import annotations

from dataclasses import dataclass


class UnknownSpanError(ValueError):
    """Raised when a name is not part of the canonical span vocabulary."""


@dataclass(frozen=True)
class SpanSpec:
    """One vocabulary entry: name, attribute schema and status semantics (see module docs)."""

    name: str
    attrs: dict[str, type]
    status: bool


# name -> (observation type, attribute schema, status flag), verbatim from the obs-model table.
_TABLE: dict[str, tuple[str, dict[str, type], bool]] = {
    "http.receive": (
        "span",
        {"method": str, "path": str, "remote": str, "content_length": int},
        True,
    ),
    "auth.verify": (
        "span",
        {"scheme": str, "key_id": str, "ok": bool},
        True,
    ),
    "body.parse": (
        "span",
        {"state_bytes": int, "question_count": int, "model_param": str, "lang_param": str},
        True,
    ),
    "lang.detect": (
        "span",
        {"lang": str, "confidence": float},
        True,
    ),
    "route.decide": (
        "span",
        {"model": str, "reason": str, "typed_workflow": bool},
        True,
    ),
    "queue.wait": (
        "span",
        {"depth_at_acquire": int},
        True,
    ),
    "model.load": (
        "span",
        {"model": str, "from": str, "bytes": int},
        True,
    ),
    "forward": (
        "generation",
        {"model": str, "questions": int, "temperature": float, "device": str},
        True,
    ),
    "serialize": (
        "span",
        {"answers": int, "bytes": int},
        True,
    ),
    "response.send": (
        "span",
        {"status": int, "bytes": int},
        True,
    ),
    "error": (
        "event",
        {"code": str, "message": str, "where": str},
        False,
    ),
}

_SPECS: dict[str, SpanSpec] = {
    name: SpanSpec(name, attrs, status) for name, (_type, attrs, status) in _TABLE.items()
}

SPAN_NAMES: frozenset[str] = frozenset(_TABLE)


def _unknown(name: str) -> UnknownSpanError:
    return UnknownSpanError(f"unknown span name {name!r}; expected one of {sorted(_SPECS)}")


def spec(name: str) -> SpanSpec:
    """Return the vocabulary entry for ``name``; unknown names raise ``UnknownSpanError``."""
    try:
        return _SPECS[name]
    except KeyError:
        raise _unknown(name) from None


def observation_type(name: str) -> str:
    """Return the observation type for ``name``: ``span``, ``generation`` or ``event``."""
    try:
        return _TABLE[name][0]
    except KeyError:
        raise _unknown(name) from None


def validate(name: str, attrs: dict) -> dict:
    """Type-check and coerce ``attrs`` against the schema for ``name``.

    Attributes may be omitted (partial records are legal: ``model.load`` fires only when a
    checkpoint loads), but every attribute that is present must type-check. ``float`` fields
    accept ``int`` values and coerce them; ``bool`` fields never accept ``int`` because
    ``bool`` is an ``int`` subclass in Python. Unknown names and attributes fail loudly.
    """
    entry = spec(name)
    out: dict = {}
    for key, value in attrs.items():
        expected = entry.attrs.get(key)
        if expected is None:
            raise ValueError(
                f"{name}: unknown attribute {key!r}; known attributes: {sorted(entry.attrs)}"
            )
        out[key] = _coerce(name, key, value, expected)
    return out


def _coerce(name: str, key: str, value: object, expected: type) -> object:
    if expected is float:
        if isinstance(value, bool):
            pass  # bool is an int subclass but is never an acceptable float attribute
        elif isinstance(value, int):
            return float(value)
        elif isinstance(value, float):
            return value
    elif expected is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif expected is bool:
        if isinstance(value, bool):
            return value
    elif expected is str:
        if isinstance(value, str):
            return value
    elif isinstance(value, expected):
        return value
    raise ValueError(f"{name}.{key}: expected {expected.__name__}, got {type(value).__name__}")
