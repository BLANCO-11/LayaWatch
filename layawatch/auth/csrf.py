"""CSRF double-submit token and cookie parsing (plan phase-3 task 3, security.md 3.2).

A readable ``lw_csrf`` cookie is issued alongside ``lw_session`` at login (and refreshed
on ``GET /api/v1/auth/me``); every mutating request from a browser session must echo the
cookie value in the ``X-CSRF-Token`` header. Missing or mismatched token is ``403``.
Key-auth and legacy admin-token mutations are exempt because they never carry the cookie
(api-reference.md section 1). Pure double submit needs no server state: an attacker on
another origin can neither read the cookie nor set the custom header without CORS approval,
and SameSite=Lax keeps cross-site requests from carrying the cookies at all.

``cookie_value`` lives here because the session and CSRF cookies are one pair and
``sessions`` must not import ``csrf``'s consumers in a cycle.
"""
from __future__ import annotations

import hmac
import secrets

from layawatch.http.types import HttpError, Request

CSRF_COOKIE = "lw_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_FAILURE_CODE = "csrf_failed"

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def cookie_value(request: Request, name: str) -> str | None:
    """Value of one cookie from the ``Cookie`` header, or None when absent/blank."""
    header = request.headers.get("cookie", "")
    for part in header.split(";"):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        key, _, value = piece.partition("=")
        if key.strip() == name and value.strip():
            return value.strip()
    return None


def new_token() -> str:
    """A fresh readable double-submit token."""
    return secrets.token_urlsafe(32)


def is_mutating(request: Request) -> bool:
    """True for the methods that require the CSRF echo when a session cookie is present."""
    return request.method.upper() in _MUTATING


def verify(request: Request) -> None:
    """Raise ``HttpError 403`` unless the ``lw_csrf`` cookie and ``X-CSRF-Token`` match.

    Non-mutating requests are exempt; every other failure (no cookie, no header, or a
    mismatch) is the same 403 so the response never distinguishes them.
    """
    if not is_mutating(request):
        return
    cookie = cookie_value(request, CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HttpError(
            403,
            CSRF_FAILURE_CODE,
            "missing or invalid CSRF token: the X-CSRF-Token header must echo the "
            "lw_csrf cookie",
        )
