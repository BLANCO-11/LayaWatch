"""Session endpoints: login, logout, me, password change, first-run setup.

Contract: docs/api-reference.md sections 1 and 10, security.md 3.1-3.2, plan phase-3
tasks 3 and 5. ``POST /api/v1/auth/login`` never enumerates users: unknown email and
wrong password produce byte-identical ``401`` bodies (verified against a dummy scrypt so
timing matches), and failures feed the in-process backoff from ``auth.login_limit`` that
refuses the sixth attempt inside 15 minutes with ``429 rate_limited``, ``Retry-After`` and
an increasing ``details.retry_after`` (the login page reads that field), audited
``auth.login_failed`` for attempts 1-5 and ``auth.login_blocked`` after that.

The login response sets both cookies the web client codes against: ``lw_session``
(HttpOnly, SameSite=Lax, Secure behind TLS) and its readable ``lw_csrf`` twin. The ASGI
bridge renders response headers from a dict and starlette lowercases the keys into raw
header lines without deduplicating, so the case-distinct ``Set-Cookie``/``set-cookie``
keys below reach the client as two separate Set-Cookie headers -- the mapping has a
single slot per exact key. ``GET /auth/me`` re-issues ``lw_csrf`` only when the request
has none, keeping the token stable for the session's life (a rotating token would race
an in-flight mutation).

``add_auth_routes`` is the single registration entry point ``__main__`` calls: it wires
these handlers, applies ``LAYWATCH_BOOTSTRAP_OWNER`` on an empty database (ignored once
any user exists), then registers the users and keys surfaces beside them.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.api.keys import add_key_routes
from layawatch.api.users import add_user_routes
from layawatch.auth import login_limit
from layawatch.auth.audit import audit
from layawatch.auth.csrf import CSRF_COOKIE, cookie_value, new_token
from layawatch.auth.login_limit import BACKOFF
from layawatch.auth.passwords import (
    PasswordPolicyError,
    check_policy,
    hash_password,
    verify_or_dummy,
    verify_password,
)
from layawatch.auth.sessions import (
    AUTH_CODE,
    gate,
    issue_session,
    resolve_session,
    revoke_other_sessions,
    revoke_session,
    session_token_from,
)
from layawatch.auth.users import insert_user, normalize_email, permissions_for
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.log import get_logger
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.config import Config
    from layawatch.http.router import Router

_log = get_logger("auth")

#: Runtime master switch for the login backoff (rate-limiting section 6.1; the row is
#: written by the rate-limit policy surface, the env default applies until then).
_RATELIMIT_ENABLED_KEY = "ratelimit.enabled"


def add_auth_routes(router: Router, *, config: Config, db_path: str | Path) -> None:
    """Register every auth/users/keys endpoint and apply the startup bootstrap owner.

    This is the one call ``__main__`` adds for the whole phase-3 auth surface; the
    bootstrap side effect runs here because registration happens exactly once at startup,
    which is when api-reference section 10 wants ``LAYWATCH_BOOTSTRAP_OWNER`` consulted.
    """
    login_limit.configure(config.ratelimit_login, config.ratelimit_login_window)
    _bootstrap_owner(config, db_path)

    def login(request: Request) -> Response:
        payload = _body(request)
        email_raw = payload.get("email")
        password = payload.get("password")
        if not isinstance(email_raw, str) or not email_raw.strip():
            raise _bad("email", "email is required")
        if not isinstance(password, str) or not password:
            raise _bad("password", "password is required")
        email = normalize_email(email_raw)
        ip = request.client_ip or ""

        conn = connect(db_path)
        try:
            limited = _login_limited(conn, config)
            retry = BACKOFF.refused(ip, email) if limited else None
            if retry is not None:
                audit(
                    conn,
                    email,
                    "auth.login_blocked",
                    target=email,
                    meta={"ip": ip, "retry_after": retry},
                )
                conn.commit()
                return _rate_limited(config, retry)
            user = conn.execute(
                "SELECT id, email, name, role, password_hash, disabled, must_change"
                " FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            stored = None
            if user is not None and not int(user["disabled"]):
                stored = user["password_hash"]
            ok = verify_or_dummy(password, stored)
            if not ok:
                if limited:
                    BACKOFF.record_failure(ip, email)
                audit(
                    conn,
                    email,
                    "auth.login_failed",
                    actor_id=str(user["id"]) if user is not None else None,
                    target=email,
                    meta={"ip": ip},
                )
                conn.commit()
                raise HttpError(401, AUTH_CODE, "invalid email or password")
            assert user is not None
            if limited:
                BACKOFF.clear(ip, email)
            # Rotation on login: the browser's previous session, if any, dies here so the
            # observed session id changes on every sign-in (acceptance criterion 11).
            previous = session_token_from(request)
            if previous:
                resolved = resolve_session(conn, previous)
                if resolved is not None:
                    revoke_session(conn, resolved[0]["id"])
            token = issue_session(conn, str(user["id"]), request, config.session_ttl)
            now = time.time()
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?", (now, user["id"])
            )
            audit(
                conn,
                str(user["email"]),
                "auth.login",
                actor_id=str(user["id"]),
                target=str(user["email"]),
                meta={"ip": ip},
            )
            conn.commit()
        finally:
            conn.close()
        secure = _behind_tls(request)
        headers = {
            "Set-Cookie": _session_cookie(token, config.session_ttl, secure),
            "set-cookie": _csrf_cookie(new_token(), config.session_ttl, secure),
        }
        return json_response(_me_payload(user), headers=headers)

    def logout(request: Request) -> Response:
        conn = connect(db_path)
        try:
            principal = gate(request, conn, config, db_path, None, action="auth.logout")
            if principal.kind != "session" or principal.session_id is None:
                raise HttpError(401, AUTH_CODE, "no session to log out of")
            revoke_session(conn, principal.session_id)
            audit(
                conn,
                principal.actor,
                "auth.logout",
                actor_id=principal.actor_id,
                target=principal.actor,
                meta={"ip": request.client_ip or ""},
            )
            conn.commit()
        finally:
            conn.close()
        # One Set-Cookie slot: clear the HttpOnly session (the stale readable csrf twin
        # is inert -- every gated request 401s before the CSRF check can matter).
        return json_response(
            {"ok": True},
            headers={"Set-Cookie": _cleared_session_cookie(_behind_tls(request))},
        )

    def me(request: Request) -> Response:
        conn = connect(db_path)
        try:
            principal = gate(request, conn, config, db_path, None)
            if principal.kind != "session":
                raise HttpError(401, AUTH_CODE, "session cookie required")
            user = principal.user
        finally:
            conn.close()
        secure = _behind_tls(request)
        csrf = cookie_value(request, CSRF_COOKIE) or new_token()
        return json_response(
            _me_payload(user), headers={"Set-Cookie": _csrf_cookie(csrf, config.session_ttl, secure)}
        )

    def change_password(request: Request) -> Response:
        payload = _body(request)
        current = payload.get("current_password")
        new = payload.get("new_password")
        if not isinstance(current, str) or not current:
            raise _bad("current_password", "'current_password' is required")
        if not isinstance(new, str) or not new:
            raise _bad("new_password", "'new_password' is required")
        conn = connect(db_path)
        try:
            principal = gate(
                request, conn, config, db_path, None, action=None
            )
            if principal.kind != "session":
                raise HttpError(401, AUTH_CODE, "session cookie required")
            user = principal.user
            if not verify_password(current, user["password_hash"]):
                raise HttpError(401, AUTH_CODE, "current password is incorrect")
            try:
                check_policy(str(user["email"]), new)
            except PasswordPolicyError as exc:
                raise _bad("new_password", str(exc)) from None
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?",
                (hash_password(new), user["id"]),
            )
            revoked = revoke_other_sessions(conn, str(user["id"]), principal.session_id)
            if revoked:
                audit(
                    conn,
                    principal.actor,
                    "session.revoked_all",
                    actor_id=principal.actor_id,
                    target=str(user["id"]),
                    meta={"revoked": revoked, "via": "password_change"},
                )
            conn.commit()
        finally:
            conn.close()
        return json_response({"ok": True})

    def setup(request: Request) -> Response:
        payload = _body(request)
        email = normalize_email(payload.get("email"))
        name_raw = payload.get("name")
        if not isinstance(name_raw, str) or not name_raw.strip():
            raise _bad("name", "'name' is required")
        password = payload.get("password")
        if not isinstance(password, str) or not password:
            raise _bad("password", "'password' is required")
        try:
            check_policy(email, password)
        except PasswordPolicyError as exc:
            raise _bad("password", str(exc)) from None
        conn = connect(db_path)
        try:
            # Serialize first-run: the immediate write lock makes a concurrent second
            # setup observe the committed owner and close with 403 instead of racing.
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                raise HttpError(403, "setup_closed", "setup has already been completed")
            created = insert_user(
                conn,
                email=email,
                name=name_raw.strip()[:120],
                role="owner",
                password_hash=hash_password(password),
                must_change=0,
            )
            audit(
                conn,
                email,
                "user.created",
                actor_id=created["id"],
                target=created["id"],
                meta={"email": email, "role": "owner", "via": "setup"},
            )
            conn.commit()
        finally:
            conn.close()
        return json_response(created)

    router.add("POST", "/api/v1/auth/login", login)
    router.add("POST", "/api/v1/auth/logout", logout)
    router.add("GET", "/api/v1/auth/me", me)
    router.add("POST", "/api/v1/auth/password", change_password)
    router.add("POST", "/api/v1/auth/setup", setup)
    add_user_routes(router, config=config, db_path=db_path)
    add_key_routes(router, config=config, db_path=db_path)


def _bootstrap_owner(config: Config, db_path: str | Path) -> None:
    """Create the first owner from ``LAYWATCH_BOOTSTRAP_OWNER`` on an empty database.

    The variable is ignored once any user exists. A malformed value or one that violates
    the password policy is logged as an error and skipped -- the setup wizard stays open,
    so a typo cannot lock the operator out of first run.
    """
    spec = config.bootstrap_owner
    if not spec:
        return
    conn = connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
            return
        email_raw, sep, password = str(spec).partition(":")
        if not sep or not email_raw.strip() or not password:
            _log.error(
                "LAYWATCH_BOOTSTRAP_OWNER must be 'email:password'; owner not created"
            )
            return
        try:
            email = normalize_email(email_raw)
            check_policy(email, password)
        except (HttpError, PasswordPolicyError) as exc:
            _log.error(f"LAYWATCH_BOOTSTRAP_OWNER rejected ({exc}); owner not created")
            return
        created = insert_user(
            conn,
            email=email,
            name=email.split("@", 1)[0],
            role="owner",
            password_hash=hash_password(password),
            must_change=0,
        )
        audit(
            conn,
            "system",
            "user.created",
            target=created["id"],
            meta={"email": email, "role": "owner", "via": "bootstrap_owner"},
        )
        conn.commit()
        _log.info(f"bootstrap owner created: {email}")
    finally:
        conn.close()


def _login_limited(conn: Any, config: Config) -> bool:
    """Master switch: the settings row when the policy surface has written it, else env."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (_RATELIMIT_ENABLED_KEY,)
    ).fetchone()
    if row is None:
        return bool(config.ratelimit_enabled)
    return str(row[0]).strip().lower() in {"1", "true", "yes", "on"}


def _rate_limited(config: Config, retry: int) -> Response:
    """429 envelope with ``Retry-After``; ``HttpError`` cannot carry custom headers."""
    message = f"too many failed login attempts; retry in {retry} seconds"
    return json_response(
        {
            "error": {
                "code": "rate_limited",
                "message": message,
                "details": {
                    "retry_after": retry,
                    "scope": "login",
                    "limit": config.ratelimit_login,
                    "window": config.ratelimit_login_window,
                },
            }
        },
        status=429,
        headers={"Retry-After": str(retry)},
    )


def _me_payload(user: Any) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "permissions": permissions_for(str(user["role"])),
    }


def _behind_tls(request: Request) -> bool:
    """Secure-cookie flag: set whenever the request reports TLS (security.md 3.2)."""
    forwarded = request.headers.get("x-forwarded-proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


def _session_cookie(token: str, ttl: int, secure: bool) -> str:
    parts = [f"lw_session={token}", f"Max-Age={int(ttl)}", "Path=/", "SameSite=Lax", "HttpOnly"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _csrf_cookie(token: str, ttl: int, secure: bool) -> str:
    parts = [f"{CSRF_COOKIE}={token}", f"Max-Age={int(ttl)}", "Path=/", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _cleared_session_cookie(secure: bool) -> str:
    parts = ["lw_session=", "Max-Age=0", "Path=/", "SameSite=Lax", "HttpOnly"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _body(request: Request) -> dict:
    payload = request.json()
    if not isinstance(payload, dict):
        raise HttpError(400, "invalid_request", "request body must be a JSON object")
    return payload


def _bad(field: str, message: str) -> HttpError:
    return HttpError(400, "invalid_request", message, details={"field": field})
