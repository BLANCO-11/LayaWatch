"""Password hashing and policy (docs/security.md 3.1, plan phase-3 task 1).

Hashes are ``scrypt$n$r$p$salt_b64$hash_b64`` with n=2**14, r=8, p=1, dklen=32 and a
16-byte random salt; verification runs the stored parameters back through
``hashlib.scrypt`` and compares with ``hmac.compare_digest``. Policy: at least 12
characters, not in the shipped ``data/common_passwords.txt`` deny list, not equal to the
email local part. No composition rules and no forced rotation, per security.md 3.1.

The deny list is loaded once (lowercased) from the repository root; when the file is
absent the list check is skipped with a warning so a partial checkout still boots.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from pathlib import Path

from layawatch.log import get_logger

_log = get_logger("auth")

_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_MIN_LENGTH = 12

#: Repository-root deny list; plan deliverable "data/common_passwords.txt" (top 10k).
_DENY_LIST_PATH = Path(__file__).resolve().parents[2] / "data" / "common_passwords.txt"

_deny_list: frozenset[str] | None = None
_dummy_hash: str | None = None


class PasswordPolicyError(ValueError):
    """A password that violates the security.md 3.1 policy; message is safe to return."""


def hash_password(password: str) -> str:
    """Return the scrypt encoding of ``password`` with a fresh 16-byte salt."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN
    )
    return "scrypt${}${}${}${}${}".format(
        _N,
        _R,
        _P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of ``password`` against a stored encoding; False when malformed."""
    if not stored:
        return False
    try:
        algorithm, n_s, r_s, p_s, salt_b64, hash_b64 = str(stored).split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        n, r, p = int(n_s), int(r_s), int(p_s)
    except (ValueError, TypeError):
        return False
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
    )
    return hmac.compare_digest(digest, expected)


def verify_or_dummy(password: str, stored: str | None) -> bool:
    """Like ``verify_password`` but always spends an scrypt round, so an unknown email and
    a wrong password cost the same (no user-enumeration timing oracle)."""
    global _dummy_hash
    if stored:
        return verify_password(password, stored)
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(24))
    verify_password(password, _dummy_hash)
    return False


def check_policy(email: str, password: str) -> None:
    """Raise ``PasswordPolicyError`` when ``password`` violates the security.md 3.1 policy."""
    if len(password) < _MIN_LENGTH:
        raise PasswordPolicyError(f"password must be at least {_MIN_LENGTH} characters")
    local_part = str(email).split("@", 1)[0].strip().lower()
    if local_part and password.strip().lower() == local_part:
        raise PasswordPolicyError("password must not equal the email local part")
    if password.strip().lower() in _load_deny_list():
        raise PasswordPolicyError("password appears in the common-password list")


def _load_deny_list() -> frozenset[str]:
    """Lowercased deny list, loaded once; an absent file logs once and matches nothing."""
    global _deny_list
    if _deny_list is None:
        try:
            lines = _DENY_LIST_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            _log.warning(f"password deny list missing at {_DENY_LIST_PATH}; check skipped")
            _deny_list = frozenset()
        else:
            _deny_list = frozenset(
                line.strip().lower()
                for line in lines
                if line.strip() and not line.lstrip().startswith("#")
            )
    return _deny_list
