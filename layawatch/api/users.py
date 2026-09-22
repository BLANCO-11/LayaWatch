"""User and session management endpoints (docs/api-reference.md section 10, plan task 4).

``GET/POST /api/v1/users``, ``PATCH/DELETE /api/v1/users/{id}``,
``GET /api/v1/users/{id}/sessions`` and system-wide ``DELETE /api/v1/sessions``. Reads
need admin, writes need owner (the shared permission table in ``auth.users``). The
security.md 2 invariants are enforced in ``auth.users``: the last enabled owner can never
be deleted, demoted or disabled; users cannot change their own role or delete themselves;
every refusal returns 403 with a committed ``result = denied`` audit row. Owner-created
users start with ``must_change = 1``. Role changes and disables revoke the target's
sessions so privilege changes rotate every session id (acceptance criterion 11).

``DELETE /api/v1/sessions`` is owner-only and revokes every live session in the system
except the acting one (api-reference section 10's "revoke all sessions except the
current one"; per-user revocation is ``POST /api/v1/auth/password`` and role changes).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.auth import login_limit
from layawatch.auth.audit import audit
from layawatch.auth.passwords import PasswordPolicyError, check_policy, hash_password
from layawatch.auth.sessions import gate, revoke_all_sessions
from layawatch.auth.users import (
    ROLES,
    apply_user_update,
    delete_user,
    fetch_user,
    insert_user,
    normalize_email,
    user_payload,
)
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.config import Config
    from layawatch.http.router import Router

_PREFIX = "/api/v1/users/"


def add_user_routes(router: Router, *, config: Config, db_path: str | Path) -> None:
    """Register the section 10 user and session endpoints on ``router``."""

    def list_users(request: Request) -> Response:
        _reject_unknown_query(request)
        conn = connect(db_path)
        try:
            gate(request, conn, config, db_path, "users.read")
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at, id"
            ).fetchall()
        finally:
            conn.close()
        items = [user_payload(row) for row in rows]
        return json_response(
            {"items": items, "next_cursor": None, "total_estimate": len(items)}
        )

    def create_user(request: Request) -> Response:
        conn = connect(db_path)
        try:
            principal = gate(
                request, conn, config, db_path, "users.write", action="user.created"
            )
            email, name, role, password_hash = _validated_create(request)
            created = insert_user(
                conn,
                email=email,
                name=name,
                role=role,
                password_hash=password_hash,
                must_change=1,
            )
            audit(
                conn,
                principal.actor,
                "user.created",
                actor_id=principal.actor_id,
                target=created["id"],
                meta={"email": email, "role": role, "must_change": True},
            )
            conn.commit()
        finally:
            conn.close()
        return json_response(created)

    def user_wildcard(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 1 and parts[0]:
            return _patch_user(request, parts[0])
        raise _unmatched(request)

    def get_user_wildcard(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 2 and parts[1] == "sessions":
            return _user_sessions(request, parts[0])
        raise _unmatched(request)

    def delete_user_route(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 1 and parts[0]:
            return _delete_user(request, parts[0])
        raise _unmatched(request)

    def revoke_other_sessions_route(request: Request) -> Response:
        conn = connect(db_path)
        try:
            principal = gate(
                request,
                conn,
                config,
                db_path,
                "sessions.revoke_all",
                action="session.revoked_all",
            )
            revoked = revoke_all_sessions(conn, principal.session_id)
            audit(
                conn,
                principal.actor,
                "session.revoked_all",
                actor_id=principal.actor_id,
                meta={"revoked": revoked, "scope": "all"},
            )
            conn.commit()
        finally:
            conn.close()
        return json_response({"revoked": revoked})

    def _patch_user(request: Request, user_id: str) -> Response:
        payload = _body(request)
        # The intended action is known from the payload before the role gate runs, so a
        # denied row names the mutation it refused rather than a generic bucket.
        action = _intent_action(payload)
        conn = connect(db_path)
        try:
            principal = gate(
                request,
                conn,
                config,
                db_path,
                "users.write",
                action=action,
                target=user_id,
            )
            target = fetch_user(conn, user_id)
            if target is None:
                raise _unknown_user(user_id)
            validated = _validated_patch(payload)
            updated = apply_user_update(conn, principal, target, validated)
            if validated:
                # An owner action on the account resets its login backoff (plan task 7).
                login_limit.clear_email(str(target["email"]))
            conn.commit()
        finally:
            conn.close()
        return json_response(updated)

    def _delete_user(request: Request, user_id: str) -> Response:
        conn = connect(db_path)
        try:
            principal = gate(
                request,
                conn,
                config,
                db_path,
                "users.write",
                action="user.deleted",
                target=user_id,
            )
            target = fetch_user(conn, user_id)
            if target is None:
                raise _unknown_user(user_id)
            delete_user(conn, principal, target)
            login_limit.clear_email(str(target["email"]))
            conn.commit()
        finally:
            conn.close()
        return json_response({"id": user_id, "deleted": True})

    def _user_sessions(request: Request, user_id: str) -> Response:
        conn = connect(db_path)
        try:
            gate(request, conn, config, db_path, "sessions.read", target=user_id)
            if fetch_user(conn, user_id) is None:
                raise _unknown_user(user_id)
            rows = conn.execute(
                "SELECT id, created_at, expires_at, last_seen, user_agent, ip"
                " FROM sessions WHERE user_id = ? AND revoked = 0 AND expires_at > ?"
                " ORDER BY created_at DESC",
                (user_id, time.time()),
            ).fetchall()
        finally:
            conn.close()
        items = [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "last_seen": row["last_seen"],
                "user_agent": row["user_agent"],
                "ip": row["ip"],
            }
            for row in rows
        ]
        return json_response(
            {"items": items, "next_cursor": None, "total_estimate": len(items)}
        )

    router.add("GET", "/api/v1/users", list_users)
    router.add("POST", "/api/v1/users", create_user)
    router.add("GET", "/api/v1/users/*", get_user_wildcard)
    router.add("PATCH", "/api/v1/users/*", user_wildcard)
    router.add("DELETE", "/api/v1/users/*", delete_user_route)
    router.add("DELETE", "/api/v1/sessions", revoke_other_sessions_route)


def _validated_create(request: Request) -> tuple[str, str, str, str]:
    """Validate the section 10 create body: ``{email, name, role, password}``."""
    payload = _body(request)
    email = normalize_email(payload.get("email"))
    name = _name(payload.get("name"))
    role = _role(payload.get("role"))
    password = payload.get("password")
    if not isinstance(password, str) or not password:
        raise _bad("password", "'password' is required")
    _policy(email, password)
    return email, name, role, hash_password(password)


def _validated_patch(payload: dict) -> dict:
    """Validate the section 10 patch fields: name, role, disabled, must_change."""
    allowed = ("name", "role", "disabled", "must_change")
    unknown = [field for field in payload if field not in allowed]
    if unknown:
        raise _bad(unknown[0], f"unknown field {unknown[0]!r}")
    if not payload:
        raise _bad("name", "no fields to update")
    out: dict[str, Any] = {}
    if "name" in payload:
        out["name"] = _name(payload["name"])
    if "role" in payload:
        out["role"] = _role(payload["role"])
    for field in ("disabled", "must_change"):
        if field in payload:
            if not isinstance(payload[field], bool):
                raise _bad(field, f"'{field}' must be true or false")
            out[field] = payload[field]
    return out


def _intent_action(payload: dict) -> str | None:
    """The documented action a PATCH intends, for a denied audit row's name."""
    if not isinstance(payload, dict):
        return None
    if "role" in payload:
        return "user.role_changed"
    if "disabled" in payload:
        return "user.disabled"
    return None


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _bad("name", "'name' is required")
    return value.strip()[:120]


def _role(value: Any) -> str:
    if value not in ROLES:
        raise _bad("role", f"'role' must be one of {', '.join(ROLES)}")
    return str(value)


def _policy(email: str, password: str) -> None:
    try:
        check_policy(email, password)
    except PasswordPolicyError as exc:
        raise _bad("password", str(exc)) from None


def _reject_unknown_query(request: Request) -> None:
    unknown = next(iter(request.query), None)
    if unknown is not None:
        raise HttpError(400, "invalid_filter", f"unknown query parameter {unknown!r}")


def _body(request: Request) -> dict:
    payload = request.json()
    if not isinstance(payload, dict):
        raise HttpError(400, "invalid_request", "request body must be a JSON object")
    return payload


def _unknown_user(user_id: str) -> HttpError:
    return HttpError(404, "not_found", f"user {user_id} not found")


def _unmatched(request: Request) -> HttpError:
    return HttpError(404, "not_found", f"no route for {request.method} {request.path}")


def _bad(field: str, message: str) -> HttpError:
    return HttpError(400, "invalid_request", message, details={"field": field})
