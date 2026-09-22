"""Capture policy tests (docs/observability-model.md section 4)."""
from __future__ import annotations

import pytest

from layawatch.obs.redact import (
    REDACT_KEYS,
    REDACTED,
    byte_len,
    capture_value,
    is_redacted_key,
    redact_structure,
    scrub_text,
)

SECRET = "hunter2-super-secret"


def test_documented_redact_key_list() -> None:
    assert set(REDACT_KEYS) == {
        "password",
        "token",
        "secret",
        "api_key",
        "authorization",
        "email",
        "phone",
        "ssn",
        "card",
    }
    assert is_redacted_key("Password")
    assert is_redacted_key("x-api-key")
    assert is_redacted_key("user_password")
    assert is_redacted_key("REFRESH_TOKEN")
    assert not is_redacted_key("user_name")


def test_capture_disabled_serializes_nothing() -> None:
    # Capture off means no payload string exists at all: neither content nor redaction
    # marker can appear anywhere, because the helper returns None.
    out = capture_value({"password": SECRET, "body": "letter text"}, enabled=False, max_bytes=100)
    assert out is None


def test_redact_structure_replaces_matching_keys_recursively() -> None:
    structure = {
        "Password": SECRET,
        "api_key": "sk-live-123",
        "body": "keep me",
        "nested": {"Authorization": "Bearer abc", "inner": {"ssn": "000-00-0000"}},
        "history": [{"token": "t-1"}, {"choice": "refund"}],
        "secret": {"whole": "dict"},  # a redacted key is replaced wholesale, whatever the type
    }
    assert redact_structure(structure) == {
        "Password": REDACTED,
        "api_key": REDACTED,
        "body": "keep me",
        "nested": {"Authorization": REDACTED, "inner": {"ssn": REDACTED}},
        "history": [{"token": REDACTED}, {"choice": "refund"}],
        "secret": REDACTED,
    }


def test_redaction_happens_whatever_the_capture_flag() -> None:
    # Structure scrubbing has no capture flag: it always replaces. capture_value just
    # decides whether the scrubbed structure is serialized at all.
    assert redact_structure({"token": "t", "keep": 1}) == {"token": REDACTED, "keep": 1}
    out = capture_value({"password": SECRET}, enabled=True, max_bytes=2048)
    assert out is not None
    assert REDACTED in out
    assert SECRET not in out


def test_scrub_strips_control_chars_except_newline_and_tab() -> None:
    raw = "a\x00b\x07c\x1bd\te\nf\x7fg"
    assert scrub_text(raw, 100) == "abcd\te\nfg"


def test_scrub_strips_before_truncating() -> None:
    # Stripped first, then cut: "a\x00bcd" -> "abcd" -> first 3 bytes == "abc".
    # Cutting first would yield "a\x00b" -> "ab" instead.
    assert scrub_text("a\x00bcd", 3) == "abc"


def test_truncation_respects_utf8_character_boundaries() -> None:
    text = "\u00e9" * 10  # ten 2-byte characters: 20 bytes total
    out = scrub_text(text, 15)  # byte 15 lands inside the 8th character
    assert out == "\u00e9" * 7  # nearest whole character at or below 15 bytes
    assert byte_len(out) == 14 <= 15
    # The result re-encodes cleanly: no split multibyte character survives.
    assert out.encode("utf-8").decode("utf-8") == out
    assert scrub_text("abcdefgh", 3) == "abc"
    assert scrub_text("abc", 100) == "abc"


def test_capture_value_truncates_to_max_bytes() -> None:
    out = capture_value({"body": "x" * 500}, enabled=True, max_bytes=32)
    assert out is not None
    assert byte_len(out) <= 32


def test_capture_value_serializes_json_and_non_serializable_leaves() -> None:
    out = capture_value({"answers": {"q1": {"choice": "refund"}}}, enabled=True, max_bytes=2048)
    assert out == '{"answers": {"q1": {"choice": "refund"}}}'
    out = capture_value({"leaf": object()}, enabled=True, max_bytes=2048)
    assert out is not None and "leaf" in out


def test_byte_len_counts_utf8_bytes() -> None:
    assert byte_len("abc") == 3
    assert byte_len("\u00e9") == 2
    assert byte_len("") == 0


def test_negative_max_bytes_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        scrub_text("x", -1)
