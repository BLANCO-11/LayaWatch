"""Session and request authentication (plan phase-3 task 2, docs/security.md 3.2).

Sessions are opaque 32-byte ``secrets.token_urlsafe`` tokens; only the SHA-256 of the
token is stored, in ``sessions.id`` (the primary key), so the database never holds a
usable credential. Lookup is by hash with absolute expiry (``LAYWATCH_SESSION_TTL``,
default 30 days) and a sliding ``last_seen`` written at most once a minute. Cookies are
``HttpOnly`` + ``SameSite=Lax`` + ``Path=/`` with ``Secure`` behind TLS
(``X-Forwarded-Proto: https``); the readable ``lw_csrf`` twin is issued by ``api/auth``.

``authenticate`` resolves a request to a ``Principal`` from the first credential present:
session cookie (browser), ``X-Admin-Token`` (legacy automation, owner-equivalent per
security.md 3.4), or ``X-API-Key``/``Bearer`` (CI reads, viewer per api-reference 1).
``gate`` is the shared entry guard every auth/users/keys handler calls: 401 without a
credential, 403 for a ``must_change`` user anywhere but ``POST /api/v1/auth/password``,
CSRF for session mutations, then the shared permission table from ``auth.users``.
``enforce_browser_session`` is the central half of that guard that
``http/middleware.instrument`` runs for every ``/api/v1`` request carrying a session
cookie, so plan task 3's "every mutating request from a browser session" holds even for
handlers this wave does not own.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.auth.csrf import cookie_value, is_mutating, verify
from layawatch.auth.users import require_permission
from layawatch.engine.keys import load_pepper, verify_key
from layawatch.http.types import HttpError, Request
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.config import Config

SESSION_COOKIE = "lw_session"
AUTH_CODE = "missing_or_invalid_credential"
MUST_CHANGE_CODE = "password_change_required"
MUST_CHANGE_PATH = "/api/v1/auth/password"

#: Unauthenticated endpoints a stale session cookie must never block (a user with an
#: expired cookie has to be able to sign in again).
_PUBLIC_PATHS = frozenset({"/api/v1/auth/login", "/api/v1/auth/setup"})

#: ``last_seen`` write throttle: sliding expiry does not need a write per request.
_TOUCH_INTERVAL = 60.0


@dataclass(frozen=True)
class Principal:
    """The authenticated caller behind a request, in every form api-reference 1 allows."""

    kind: str  # session | admin_token | api_key
    role: str  # owner | admin | viewer
    actor: str  # audit actor: email, "legacy-admin-token" or "key:<id>"
    actor_id: str | None = None  # user id for session principals
    user: Any = None  # user row (id/email/name/role/password_hash/must_change)
    session_id: str | None = None
    key_id: str | None = None


def session_token_from(request: Request) -> str | None:
    """The raw ``lw_session`` cookie, or None (never logged, never echoed)."""
    return cookie_value(request, SESSION_COOKIE)


def token_digest(token: str) -> str:
    """SHA-256 of a session token: the only form stored (security.md 3.2)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(
    conn: sqlite3.Connection,
    user_id: str,
    request: Request,
    ttl: int,
    *,
    now: float | None = None,
) -> str:
    """Create a session row for ``user_id`` and return the raw token (stored only hashed)."""
    token = secrets.token_urlsafe(32)
    issued = time.time() if now is None else now
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, expires_at, last_seen, user_agent, ip)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            token_digest(token),
            user_id,
            issued,
            issued + float(ttl),
            issued,
            request.headers.get("user-agent", "")[:256] or None,
            request.client_ip or None,
        ),
    )
    return token


def resolve_session(
    conn: sqlite3.Connection, token: str, *, now: float | None = None
) -> tuple[dict, dict] | None:
    """``(session, user)`` dicts for a live token; None when unknown, revoked, expired or
    the user is disabled. Slides ``last_seen`` at most once a minute."""
    if not token:
        return None
    current = time.time() if now is None else now
    row = conn.execute(
        "SELECT s.id AS sid, s.user_id AS uid, s.expires_at, s.last_seen,"
        " u.email, u.name, u.role, u.password_hash, u.must_change"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.id = ? AND s.revoked = 0 AND s.expires_at > ? AND u.disabled = 0",
        (token_digest(token), current),
    ).fetchone()
    if row is None:
        return None
    if current - float(row["last_seen"]) >= _TOUCH_INTERVAL:
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE id = ?", (current, row["sid"])
        )
    session = {
        "id": row["sid"],
        "user_id": row["uid"],
        "expires_at": row["expires_at"],
        "last_seen": row["last_seen"],
    }
    user = {
        "id": row["uid"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "password_hash": row["password_hash"],
        "must_change": row["must_change"],
    }
    return session, user


def revoke_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """Revoke one session by its stored id; True when a live row changed."""
    cursor = conn.execute(
        "UPDATE sessions SET revoked = 1 WHERE id = ? AND revoked = 0", (session_id,)
    )
    return cursor.rowcount > 0


def revoke_other_sessions(
    conn: sqlite3.Connection, user_id: str, keep_session_id: str | None
) -> int:
    """Revoke every live session of ``user_id`` except ``keep_session_id``; the count."""
    if keep_session_id is None:
        cursor = conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0", (user_id,)
        )
    else:
        cursor = conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE user_id = ? AND id != ? AND revoked = 0",
            (user_id, keep_session_id),
        )
    return cursor.rowcount


def revoke_all_sessions(conn: sqlite3.Connection, keep_session_id: str | None) -> int:
    """Revoke every live session in the system except ``keep_session_id``; the count."""
    if keep_session_id is None:
        cursor = conn.execute("UPDATE sessions SET revoked = 1 WHERE revoked = 0")
    else:
        cursor = conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE id != ? AND revoked = 0",
            (keep_session_id,),
        )
    return cursor.rowcount


def authenticate(request: Request, config: Config, db_path: str | Path) -> Principal | None:
    """Resolve the request's first credential to a Principal; None when none verifies.

    Order: session cookie, then ``X-Admin-Token`` (owner-equivalent, security.md 3.4),
    then ``X-API-Key``/``Authorization: Bearer`` (viewer on reads, api-reference 1). A
    resolved session is cached on the request so a handler behind the middleware gate
    never resolves the same cookie twice.
    """
    cached = getattr(request, "principal", None)
    if isinstance(cached, Principal):
        return cached
    token = session_token_from(request)
    if token:
        conn = connect(db_path)
        try:
            resolved = resolve_session(conn, token)
        finally:
            conn.close()
        if resolved is None:
            return None
        session, user = resolved
        principal = _session_principal(session, user)
        request.principal = principal  # type: ignore[attr-defined]
        return principal
    admin_token = config.admin_token
    presented_admin = request.headers.get("x-admin-token", "")
    if admin_token and presented_admin and hmac.compare_digest(presented_admin, admin_token):
        principal = Principal(kind="admin_token", role="owner", actor="legacy-admin-token")
        request.principal = principal  # type: ignore[attr-defined]
        return principal
    presented_key = _presented_key(request)
    if presented_key:
        conn = connect(db_path)
        try:
            key_id = verify_key(conn, presented_key, load_pepper(Path(db_path).parent))
        finally:
            conn.close()
        if key_id is None:
            return None
        principal = Principal(
            kind="api_key", role="viewer", actor=f"key:{key_id}", key_id=key_id
        )
        request.principal = principal  # type: ignore[attr-defined]
        return principal
    return None


def gate(
    request: Request,
    conn: sqlite3.Connection,
    config: Config,
    db_path: str | Path,
    permission: str | None,
    *,
    action: str | None = None,
    target: str | None = None,
) -> Principal:
    """Shared handler guard: credential, must_change, CSRF, then the permission table.

    401 when no credential verifies. A ``must_change`` user is refused everywhere except
    ``POST /api/v1/auth/password`` (plan task 4). A session mutation must echo the
    ``lw_csrf`` cookie (403). A role failure commits its ``result = denied`` audit row
    before raising 403 (security.md invariant 3). ``permission=None`` skips the role
    check for endpoints any authenticated role may call (logout, me).
    """
    principal = authenticate(request, config, db_path)
    if principal is None:
        raise HttpError(401, AUTH_CODE, "missing or invalid credential")
    if principal.kind == "session":
        if int(principal.user["must_change"] or 0) and not (
            request.method == "POST" and request.path == MUST_CHANGE_PATH
        ):
            raise HttpError(
                403,
                MUST_CHANGE_CODE,
                "a new password is required before any other action",
            )
        verify(request)
    if permission is not None:
        require_permission(conn, principal, permission, action=action, target=target)
    return principal


def enforce_browser_session(request: Request, db_path: str | Path) -> None:
    """Central half of :func:`gate` for ``http/middleware.instrument``.

    Requests with no ``lw_session`` cookie pass through untouched (API-key, admin-token
    and unauthenticated callers keep their current behavior). With a cookie: an
    invalid/expired session is 401, a ``must_change`` user is 403 everywhere except
    ``POST /api/v1/auth/password``, and a mutating request must echo ``lw_csrf`` in
    ``X-CSRF-Token``. A valid session is cached on the request for the handler's gate.
    The public login/setup endpoints are exempt so a stale cookie cannot block sign-in.
    """
    if not request.path.startswith("/api/v1/") or request.path in _PUBLIC_PATHS:
        return
    token = session_token_from(request)
    if not token:
        return
    conn = connect(db_path)
    try:
        resolved = resolve_session(conn, token)
    finally:
        conn.close()
    if resolved is None:
        raise HttpError(401, AUTH_CODE, "missing or invalid credential")
    session, user = resolved
    if int(user["must_change"] or 0) and not (
        request.method == "POST" and request.path == MUST_CHANGE_PATH
    ):
        raise HttpError(
            403, MUST_CHANGE_CODE, "a new password is required before any other action"
        )
    if is_mutating(request):
        verify(request)
    request.principal = _session_principal(session, user)  # type: ignore[attr-defined]


def _session_principal(session: dict, user: dict) -> Principal:
    return Principal(
        kind="session",
        role=str(user["role"]),
        actor=str(user["email"]),
        actor_id=str(user["id"]),
        user=user,
        session_id=str(session["id"]),
    )


def _presented_key(request: Request) -> str | None:
    """Engine-client credential from ``X-API-Key`` or ``Authorization: Bearer``."""
    header = request.headers.get("x-api-key", "")
    if header.strip():
        return header.strip()
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer ") and authorization[7:].strip():
        return authorization[7:].strip()
    return None
