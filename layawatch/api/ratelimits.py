"""Rate limit management surface (docs/rate-limiting.md 6.1, 6.4, 7).

``GET /api/v1/ratelimits`` answers the effective policy - settings rows over config
defaults (section 9) - plus the per-key overrides on ``api_keys``; viewer+ (settings.read).
``POST /api/v1/ratelimits`` applies a partial, validated update (auth.ratelimit), audits
``ratelimit.updated`` with before and after values, and stores the rows so the change
holds from the next request with no restart (admin+, settings.write).
``GET /api/v1/ratelimits/usage`` streams the live bucket panel: top 10 subjects by usage
ratio with usage above 0 (section 5), viewer+.
``POST /api/v1/ratelimits/reset`` clears one ``{subject, scope}`` bucket or every bucket
with ``{"all": true}``, audited ``ratelimit.reset`` (admin+, settings.write).

The login window edit is pushed into ``auth.login_limit`` on the spot: that counter is the
one in-process consumer of these rows today, and acceptance 7 requires the new value to
bind on the next request. ``ratelimit.enabled`` is already read from settings per request
by ``api/auth``.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.auth import login_limit
from layawatch.auth.audit import audit
from layawatch.auth.ratelimit import (
    REGISTRY,
    SCOPES,
    PolicyError,
    effective_policy,
    upsert_policy,
    validated_update,
)
from layawatch.auth.sessions import gate
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store.db import connect

if TYPE_CHECKING:
    import sqlite3

    from layawatch.config import Config
    from layawatch.http.router import Router

#: Panel cap: section 5 promises the top 10 subjects, sorted by usage ratio.
_USAGE_TOP = 10

_RESET_FIELDS = ("all", "subject", "scope")


def add_ratelimit_routes(router: Router, *, config: Config, db_path: str | Path) -> None:
    """Register the section 7 rate limit endpoints on ``router``."""

    def get_policy(request: Request) -> Response:
        _reject_unknown_query(request)
        conn = connect(db_path)
        try:
            gate(request, conn, config, db_path, "settings.read")
            payload = _payload(conn, config)
        finally:
            conn.close()
        return json_response(payload)

    def update_policy(request: Request) -> Response:
        try:
            updates = validated_update(request.json())
        except PolicyError as exc:
            raise HttpError(
                400, "invalid_request", exc.message, details={"field": exc.field}
            ) from None
        conn = connect(db_path)
        try:
            principal = gate(
                request, conn, config, db_path, "settings.write", action="ratelimit.updated"
            )
            current = effective_policy(conn, config)
            before = {name: current[name] for name in updates}
            merged = {**current, **updates}
            upsert_policy(conn, updates, actor=principal.actor)
            audit(
                conn,
                principal.actor,
                "ratelimit.updated",
                actor_id=principal.actor_id,
                target="policy",
                meta={
                    "before": before,
                    "after": {name: merged[name] for name in updates},
                },
            )
            conn.commit()
            payload = {**merged, "keys": _key_overrides(conn)}
        finally:
            conn.close()
        if "login" in updates or "login_window" in updates:
            login_limit.configure(int(merged["login"]), int(merged["login_window"]))
        return json_response(payload)

    def usage(_request: Request) -> Response:
        conn = connect(db_path)
        try:
            gate(_request, conn, config, db_path, "settings.read")
        finally:
            conn.close()
        rows = REGISTRY.usage()
        return json_response(
            {
                "items": rows[:_USAGE_TOP],
                "next_cursor": None,
                "total_estimate": len(rows),
            }
        )

    def reset(request: Request) -> Response:
        form = _validated_reset(request)
        conn = connect(db_path)
        try:
            principal = gate(
                request, conn, config, db_path, "settings.write", action="ratelimit.reset"
            )
            if form.get("all"):
                cleared = REGISTRY.reset(all_=True)
                target = "all"
            else:
                cleared = REGISTRY.reset(str(form["subject"]), str(form["scope"]))
                target = str(form["subject"])
            audit(
                conn,
                principal.actor,
                "ratelimit.reset",
                actor_id=principal.actor_id,
                target=target,
                meta=dict(form),
            )
            conn.commit()
        finally:
            conn.close()
        return json_response({"reset": cleared})

    router.add("GET", "/api/v1/ratelimits", get_policy)
    router.add("POST", "/api/v1/ratelimits", update_policy)
    router.add("GET", "/api/v1/ratelimits/usage", usage)
    router.add("POST", "/api/v1/ratelimits/reset", reset)


def _payload(conn: sqlite3.Connection, config: Config) -> dict:
    return {**effective_policy(conn, config), "keys": _key_overrides(conn)}


def _key_overrides(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, prefix, rate_limit_per_min, burst FROM api_keys ORDER BY created_at, id"
    ).fetchall()
    return [dict(row) for row in rows]


def _validated_reset(request: Request) -> dict[str, Any]:
    """The 6.7 body: ``{subject, scope}`` or ``{all: true}``, nothing else."""
    payload = request.json()
    if not isinstance(payload, dict):
        raise _bad("body", "request body must be a JSON object")
    unknown = next((name for name in payload if name not in _RESET_FIELDS), None)
    if unknown is not None:
        raise _bad(unknown, f"unknown field {unknown!r}")
    if payload.get("all") is True:
        if "subject" in payload or "scope" in payload:
            raise _bad("all", "'all' cannot be combined with 'subject' or 'scope'")
        return {"all": True}
    if "all" in payload:
        raise _bad("all", "'all' must be true when present")
    subject = payload.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise _bad("subject", "'subject' is required")
    scope = payload.get("scope")
    if not isinstance(scope, str) or scope not in SCOPES:
        raise _bad("scope", f"'scope' must be one of {list(SCOPES)}")
    return {"subject": subject.strip()[:200], "scope": scope}


def _reject_unknown_query(request: Request) -> None:
    unknown = next(iter(request.query), None)
    if unknown is not None:
        raise HttpError(400, "invalid_filter", f"unknown query parameter {unknown!r}")


def _bad(field: str, message: str) -> HttpError:
    return HttpError(400, "invalid_request", message, details={"field": field})
