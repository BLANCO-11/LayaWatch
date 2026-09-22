"""API key pepper, hashing, verification and the auth toggle (docs/security.md 3.3).

Secrets are ``lay_`` + 32 base62 characters, shown once and stored only as a digest keyed by the
server pepper in ``LAYA_STATE_DIR/secret.key`` (created 0600 on first use). Lookup is by the
8-char public prefix; every digest comparison goes through ``hmac.compare_digest``.

Verification accepts the two stored formats that can exist in ``api_keys.hash``:

- ``HMAC-SHA256(pepper, secret)`` hex -- new keys, per the column comment and api-reference
  section 9;
- plain ``SHA256(secret)`` hex -- what ``legacy/serve.py`` wrote into ``api_keys.json``. The
  import (engine/legacy_state.py) keeps those digests verbatim, so any plaintext that verified
  against the legacy server verifies after import; both comparisons run unconditionally (no
  short-circuit on the first format).

Documented deviation from docs/security.md 3.3: that section wants ``request_count``/``last_used``
updated in the writer batch. The pinned Phase 1 contract for ``verify_key`` instead performs
SELECT + UPDATE in one short transaction -- keys are authentication data, not observability rows,
and keeping the bookkeeping inside the verify path leaves no cross-thread ordering to reason
about for a table that changes only on admin actions and verification.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import string
import time
from pathlib import Path

# Settings row arming key auth for /predict and /route. docs/api-reference.md section 9 names the
# arming surface POST /api/v1/keys/auth; settings keys are dotted namespaces (rate-limiting policy
# lives under ratelimit.* per docs/rate-limiting.md 7), so the row is keys.auth.
AUTH_SETTINGS_KEY = "keys.auth"

_PREFIX_LEN = 8
_SECRET_LEN = 32
_BASE62 = string.digits + string.ascii_uppercase + string.ascii_lowercase
_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})


def load_pepper(state_dir: str | os.PathLike[str]) -> bytes:
    """Return the server pepper, creating ``state_dir/secret.key`` with mode 0600 on first use.

    The file is read verbatim (64 hex chars of fresh entropy); an existing file is never
    rewritten or re-moded, because replacing it invalidates every stored HMAC -- and every
    session, if the operator also points LAYWATCH_SESSION_SECRET at the same file.
    """
    path = Path(state_dir) / "secret.key"
    payload = secrets.token_hex(32).encode("ascii")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, "rb") as fh:
            return fh.read()
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass  # a half-written secret must not survive to become the pepper
        raise
    return payload


def generate_secret() -> str:
    """A fresh engine-client secret: ``lay_`` + 32 base62 chars, returned once, never stored."""
    return "lay_" + "".join(secrets.choice(_BASE62) for _ in range(_SECRET_LEN))


def prefix_of(secret: str) -> str:
    """The 8-char public prefix stored on the row for lookup (api-reference.md section 9)."""
    return str(secret)[:_PREFIX_LEN]


def hash_secret(secret: str, pepper: bytes) -> str:
    """``HMAC-SHA256(pepper, secret)`` as lowercase hex (docs/security.md 3.3)."""
    return hmac.new(pepper, str(secret).encode("utf-8"), hashlib.sha256).hexdigest()


def verify_key(
    conn: sqlite3.Connection,
    presented: str,
    pepper: bytes,
) -> str | None:
    """Return the key id whose stored digest matches ``presented``; None if nothing matches.

    Candidates come from an index lookup on the 8-char prefix -- of either the current row
    value or the rotation-grace ``prev_prefix`` (rows sharing a prefix are all checked;
    revoked rows never are). Each candidate is compared in constant time against BOTH
    stored formats -- the peppered HMAC for new keys and the plain SHA-256 carried over from
    the legacy file -- so pre-import plaintexts keep verifying, with no short-circuit between
    the two checks. A rotated key's previous digest (``prev_hash``) joins the comparison
    while ``prev_expires_at`` is in the future, which is the 5-minute rotation grace of
    security.md 3.3; after the deadline it is ignored without being cleared. On a match the
    row's ``last_used``/``request_count`` move in the same short transaction as the read
    (see the module docstring for the security.md 3.3 deviation).
    """
    secret = str(presented)
    if not secret:
        return None
    peppered = hash_secret(secret, pepper)
    legacy = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    prefix = prefix_of(secret)
    with conn:
        rows = conn.execute(
            "SELECT id, hash, prev_hash, prev_expires_at FROM api_keys"
            " WHERE (prefix = ? OR prev_prefix = ?) AND revoked_at IS NULL",
            (prefix, prefix),
        ).fetchall()
        now = time.time()
        for row in rows:
            stored = row[1]
            matched = hmac.compare_digest(stored, peppered) | hmac.compare_digest(
                stored, legacy
            )
            # Rotation grace: the previous digest stays valid until prev_expires_at
            # (5 minutes, security.md 3.3); the prefix lookup above matches either slot.
            prev_hash, prev_expires_at = row[2], row[3]
            if prev_hash and prev_expires_at is not None and prev_expires_at > now:
                matched = matched | hmac.compare_digest(
                    prev_hash, peppered
                ) | hmac.compare_digest(prev_hash, legacy)
            if matched:
                conn.execute(
                    "UPDATE api_keys SET last_used = ?, request_count = request_count + 1"
                    " WHERE id = ?",
                    (time.time(), row[0]),
                )
                return row[0]
    return None


def auth_armed(conn: sqlite3.Connection) -> bool:
    """True when the ``keys.auth`` settings row arms key auth; a missing row means never armed.

    The row is written as ``true``/``false`` (the legacy api_keys.json spelling); the reader also
    accepts 1/yes/on, matching how Config parses booleans. Legacy's default state is
    ``enabled: false``, so an empty database is unarmed.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (AUTH_SETTINGS_KEY,)
    ).fetchone()
    if row is None:
        return False
    return str(row[0]).strip().lower() in _BOOL_TRUE
