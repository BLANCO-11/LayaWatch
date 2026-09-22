"""Redaction, truncation and payload capture helpers (docs/observability-model.md section 4).

Order of operations for captured payloads: redact matching keys first, serialize to JSON,
then strip control characters and cut at a UTF-8 character boundary. Stored payloads are
therefore always decodable and never contain a secret from ``REDACT_KEYS``, even when the
secret would have survived truncation. With capture disabled nothing is serialized at all.
"""
from __future__ import annotations

import json
import unicodedata
from typing import Any

REDACTED = "[redacted]"

# Key patterns from the docs capture policy. Matching is case-insensitive and separator-blind
# (lowercase, then drop "_"/"-"), so "api_key", "apiKey" and "X-API-Key" all match the same
# pattern, and "user_password" is covered by its canonical substring.
REDACT_KEYS: frozenset[str] = frozenset(
    {
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
)
_NORMALIZED_KEYS = frozenset(pattern.replace("_", "") for pattern in REDACT_KEYS)


def byte_len(s: str) -> int:
    """UTF-8 byte length of ``s``; capture limits are counted in bytes, not characters."""
    return len(s.encode("utf-8"))


def is_redacted_key(key: str) -> bool:
    """True when ``key`` contains a ``REDACT_KEYS`` pattern (case/separator-insensitive)."""
    normalized = key.lower().replace("_", "").replace("-", "")
    return any(pattern in normalized for pattern in _NORMALIZED_KEYS)


def redact_structure(value: Any) -> Any:
    """Return ``value`` with every redacted-key value replaced by ``REDACTED``.

    Recurses through dicts, lists and tuples; a redacted key's value is replaced wholesale
    whatever its type. There is no capture flag: structures are scrubbed whenever they are
    scrubbed.
    """
    if isinstance(value, dict):
        out: dict = {}
        for key, item in value.items():
            if isinstance(key, str) and is_redacted_key(key):
                out[key] = REDACTED
            else:
                out[key] = redact_structure(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_structure(item) for item in value]
    return value


def scrub_text(s: str, max_bytes: int) -> str:
    """Strip control characters (keeping ``\\n`` and ``\\t``), then cut at ``max_bytes``.

    Truncation happens after stripping and never splits a UTF-8 character: the byte prefix
    is decoded leniently, so a multibyte character straddling the boundary is dropped whole
    and the result always re-encodes to at most ``max_bytes`` bytes.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")
    cleaned = "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    encoded = cleaned.encode("utf-8")
    if len(encoded) > max_bytes:
        cleaned = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return cleaned


def capture_value(value: Any, *, enabled: bool, max_bytes: int) -> str | None:
    """Serialize ``value`` for storage: ``None`` when capture is off.

    When enabled the structure is redacted, JSON-serialized (non-serializable leaves become
    their ``str()`` form) and scrubbed down to ``max_bytes`` UTF-8 bytes.
    """
    if not enabled:
        return None
    payload = json.dumps(redact_structure(value), ensure_ascii=False, default=str)
    return scrub_text(payload, max_bytes)
