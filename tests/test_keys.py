"""Contract tests for engine/keys.py: pepper file, secret shape, dual-format verify, auth toggle."""
from __future__ import annotations

import hashlib
import hmac
import os
import string
import time

import pytest

from layawatch.engine.keys import (
    AUTH_SETTINGS_KEY,
    auth_armed,
    generate_secret,
    hash_secret,
    load_pepper,
    prefix_of,
    verify_key,
)
from layawatch.store.db import connect, migrate

PEPPER = b"test-pepper"


@pytest.fixture()
def conn(tmp_path):
    database = connect(tmp_path / "state.sqlite3")
    migrate(database)
    yield database
    database.close()


def insert_key(
    conn,
    key_id: str,
    secret: str,
    *,
    revoked_at: float | None = None,
    legacy: bool = False,
) -> None:
    digest = (
        hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if legacy
        else hash_secret(secret, PEPPER)
    )
    conn.execute(
        "INSERT INTO api_keys (id, name, prefix, hash, created_at, revoked_at, request_count)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (key_id, "ci", prefix_of(secret), digest, time.time(), revoked_at),
    )
    conn.commit()


def request_count(conn, key_id: str) -> int:
    row = conn.execute(
        "SELECT request_count FROM api_keys WHERE id = ?", (key_id,)
    ).fetchone()
    return row[0]


def test_load_pepper_creates_secret_key_with_mode_0600(tmp_path) -> None:
    pepper = load_pepper(tmp_path)
    path = tmp_path / "secret.key"
    assert path.exists()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert len(pepper) >= 32
    assert pepper == path.read_bytes()
    # Stable across calls: the pepper is generated once, never rotated in place.
    assert load_pepper(tmp_path) == pepper
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_load_pepper_reads_an_existing_file_verbatim(tmp_path) -> None:
    managed = b"operator-managed-pepper-material"
    (tmp_path / "secret.key").write_bytes(managed)
    assert load_pepper(tmp_path) == managed
    assert (tmp_path / "secret.key").read_bytes() == managed


def test_generate_secret_is_lay_prefix_plus_32_base62_chars() -> None:
    alphabet = set(string.digits + string.ascii_uppercase + string.ascii_lowercase)
    seen = {generate_secret() for _ in range(50)}
    assert len(seen) == 50  # fresh every call
    for secret in seen:
        assert secret.startswith("lay_")
        assert len(secret) == 4 + 32
        assert set(secret[4:]) <= alphabet


def test_prefix_of_is_the_first_eight_characters() -> None:
    assert prefix_of("lay_abcdefgh") == "lay_abcd"
    secret = generate_secret()
    assert prefix_of(secret) == secret[:8]
    assert prefix_of("lay_x") == "lay_x"  # short input never raises


def test_hash_secret_is_hmac_sha256_keyed_by_the_pepper() -> None:
    expected = hmac.new(PEPPER, b"lay_secret", hashlib.sha256).hexdigest()
    assert hash_secret("lay_secret", PEPPER) == expected
    assert len(expected) == 64 and expected == expected.lower()
    assert hash_secret("lay_secret", b"another-pepper") != expected
    assert hash_secret("lay_other", PEPPER) != expected


def test_verify_roundtrip_returns_the_id_and_updates_bookkeeping(conn) -> None:
    secret = generate_secret()
    insert_key(conn, "k1", secret)

    assert verify_key(conn, secret, PEPPER) == "k1"
    row = conn.execute(
        "SELECT last_used, request_count FROM api_keys WHERE id = 'k1'"
    ).fetchone()
    assert row[1] == 1
    assert row[0] is not None and row[0] <= time.time() + 1

    assert verify_key(conn, secret, PEPPER) == "k1"
    assert request_count(conn, "k1") == 2


def test_verify_rejects_wrong_secrets_and_counts_nothing(conn) -> None:
    secret = generate_secret()
    insert_key(conn, "k1", secret)

    same_prefix_wrong_tail = secret[:10] + ("0" if secret[10] != "0" else "1") + secret[11:]
    assert prefix_of(same_prefix_wrong_tail) == prefix_of(secret)
    assert verify_key(conn, same_prefix_wrong_tail, PEPPER) is None
    assert verify_key(conn, generate_secret(), PEPPER) is None
    assert verify_key(conn, "", PEPPER) is None
    assert request_count(conn, "k1") == 0


def test_verify_rejects_a_revoked_key(conn) -> None:
    secret = generate_secret()
    insert_key(conn, "k1", secret, revoked_at=time.time())
    assert verify_key(conn, secret, PEPPER) is None
    assert request_count(conn, "k1") == 0


def test_verify_accepts_a_legacy_unpeppered_digest(conn) -> None:
    # Exact pre-v0.1.0 secret shape: "laya_" + token_hex(24), stored as plain sha256.
    legacy_secret = "laya_" + "0123456789abcdef" * 3
    insert_key(conn, "legacy1", legacy_secret, legacy=True)
    # The stored digest ignores the pepper, so any pepper still verifies the legacy plaintext.
    assert verify_key(conn, legacy_secret, PEPPER) == "legacy1"
    assert request_count(conn, "legacy1") == 1
    assert verify_key(conn, legacy_secret, b"unrelated-pepper") == "legacy1"
    assert request_count(conn, "legacy1") == 2
    wrong = legacy_secret[:10] + ("e" if legacy_secret[10] != "e" else "d") + legacy_secret[11:]
    assert verify_key(conn, wrong, PEPPER) is None
    assert request_count(conn, "legacy1") == 2  # the mismatched candidate counted nothing


def test_verify_finds_the_right_key_when_two_share_a_prefix(conn) -> None:
    first = "lay_abcd" + "X" * 28
    second = "lay_abcd" + "Y" * 28
    assert prefix_of(first) == prefix_of(second)
    insert_key(conn, "ka", first)
    insert_key(conn, "kb", second)

    assert verify_key(conn, first, PEPPER) == "ka"
    assert verify_key(conn, second, PEPPER) == "kb"
    assert request_count(conn, "ka") == 1
    assert request_count(conn, "kb") == 1


def test_auth_armed_settings_roundtrip(conn) -> None:
    # Fresh database: no row means never armed, the legacy default (enabled: false).
    assert auth_armed(conn) is False

    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, 'true', ?)",
        (AUTH_SETTINGS_KEY, time.time()),
    )
    conn.commit()
    assert auth_armed(conn) is True
    # The row lives under the name other phases read (api-reference.md section 9: keys/auth).
    row = conn.execute("SELECT value FROM settings WHERE key = 'keys.auth'").fetchone()
    assert row[0] == "true"

    conn.execute("UPDATE settings SET value = 'false' WHERE key = ?", (AUTH_SETTINGS_KEY,))
    conn.commit()
    assert auth_armed(conn) is False
