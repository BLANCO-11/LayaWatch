"""API key surface: list, create, rotate, revoke, arm/disarm, per-key limits.

Contract: docs/api-reference.md sections 9 and 9.1, security.md 3.3, plan phase-3 task 6.
Secrets are ``lay_`` + 32 base62 chars stored as ``HMAC-SHA256(pepper, secret)`` with the
pepper from ``state_dir/secret.key`` (engine/keys.py); the plaintext is returned exactly
once and never stored or logged. Rotation keeps the previous digest valid for a
five-minute grace window (the 0002 migration's ``prev_*`` columns, checked by
``engine/keys.verify_key``); revoke is a soft delete that clears both digests immediately.

The legacy ``/admin/api/keys`` and ``/admin/api/auth`` shims (api/legacy.py) forward onto
these exact paths, so the body-variant ``DELETE /api/v1/keys`` (id in the JSON body, what
legacy scripts send) is registered beside the documented ``DELETE /api/v1/keys/{id}``,
and ``GET /api/v1/keys/auth`` answers the armed state the legacy GET expects.

Every handler runs the shared :func:`layawatch.auth.sessions.gate`: 401 without a
credential, CSRF for session mutations, role from the shared permission table (viewer
lists, admin mutates), and a committed ``result = denied`` audit row on refusals.
"""
from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.auth.audit import audit
from layawatch.auth.sessions import gate
from layawatch.engine.keys import (
    AUTH_SETTINGS_KEY,
    auth_armed,
    generate_secret,
    hash_secret,
    load_pepper,
    prefix_of,
)
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.config import Config
    from layawatch.http.router import Router

#: Rotation grace: the previous secret stays valid this long (security.md 3.3).
GRACE_SECONDS = 300.0

_PREFIX = "/api/v1/keys/"
_NOTE_ONCE = "store this key now; it is not shown again"
_LIMIT_FIELDS = ("rate_limit_per_min", "burst")


def add_key_routes(router: Router, *, config: Config, db_path: str | Path) -> None:
    """Register the section 9/9.1 key endpoints on ``router``."""

    def list_keys(request: Request) -> Response:
        _reject_unknown_query(request)
        conn = connect(db_path)
        try:
            gate(request, conn, config, db_path, "keys.list")
            rows = conn.execute(
                "SELECT id, name, prefix, created_at, last_used, revoked_at,"
                " request_count, rate_limit_per_min, burst FROM api_keys"
                " ORDER BY created_at DESC, id"
            ).fetchall()
        finally:
            conn.close()
        items = [_key_payload(row) for row in rows]
        return json_response(
            {"items": items, "next_cursor": None, "total_estimate": len(items)}
        )

    def create_key(request: Request) -> Response:
        payload = _body(request)
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _bad("name", "'name' is required")
        limits = _limits(payload)
        conn = connect(db_path)
        try:
            principal = gate(
                request, conn, config, db_path, "keys.write", action="key.created"
            )
            key_id = secrets.token_hex(6)
            secret = generate_secret()
            now = time.time()
            conn.execute(
                "INSERT INTO api_keys (id, name, prefix, hash, created_at,"
                " rate_limit_per_min, burst) VALUES (?,?,?,?,?,?,?)",
                (
                    key_id,
                    name.strip()[:120],
                    prefix_of(secret),
                    hash_secret(secret, load_pepper(Path(db_path).parent)),
                    now,
                    limits["rate_limit_per_min"],
                    limits["burst"],
                ),
            )
            audit(
                conn,
                principal.actor,
                "key.created",
                actor_id=principal.actor_id,
                target=key_id,
                meta={"name": name.strip(), "prefix": prefix_of(secret)},
            )
            conn.commit()
        finally:
            conn.close()
        return json_response(
            {
                "id": key_id,
                "name": name.strip(),
                "key": secret,
                "prefix": prefix_of(secret),
                "rate_limit_per_min": limits["rate_limit_per_min"],
                "burst": limits["burst"],
                "note": _NOTE_ONCE,
            }
        )

    def delete_by_path(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 1 and parts[0]:
            return _revoke(request, parts[0])
        raise _unmatched(request)

    def delete_by_body(request: Request) -> Response:
        payload = _body(request)
        key_id = payload.get("id")
        if not isinstance(key_id, str) or not key_id:
            raise _bad("id", "'id' is required")
        return _revoke(request, key_id)

    def post_wildcard(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 2 and parts[0] and parts[1] == "rotate":
            return _rotate(request, parts[0])
        raise _unmatched(request)

    def patch_key(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 1 and parts[0]:
            return _set_limits(request, parts[0])
        raise _unmatched(request)

    def get_auth_state(request: Request) -> Response:
        _reject_unknown_query(request)
        conn = connect(db_path)
        try:
            gate(request, conn, config, db_path, "keys.read_auth")
            enabled = auth_armed(conn)
        finally:
            conn.close()
        return json_response({"enabled": enabled})

    def set_auth_state(request: Request) -> Response:
        payload = _body(request)
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise _bad("enabled", "'enabled' must be true or false")
        conn = connect(db_path)
        try:
            principal = gate(
                request,
                conn,
                config,
                db_path,
                "keys.write",
                action="key.auth_changed",
                target="keys.auth",
            )
            if enabled and not _has_active_key(conn):
                raise _bad(
                    "enabled", "cannot arm key auth without at least one active key"
                )
            conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (
                    AUTH_SETTINGS_KEY,
                    "true" if enabled else "false",
                    time.time(),
                    principal.actor,
                ),
            )
            audit(
                conn,
                principal.actor,
                "key.auth_changed",
                actor_id=principal.actor_id,
                target="keys.auth",
                meta={"enabled": enabled},
            )
            conn.commit()
        finally:
            conn.close()
        return json_response({"enabled": enabled})

    def _revoke(request: Request, key_id: str) -> Response:
        conn = connect(db_path)
        try:
            principal = gate(
                request,
                conn,
                config,
                db_path,
                "keys.write",
                action="key.revoked",
                target=key_id,
            )
            row = _load(conn, key_id)
            if row["revoked_at"] is None:
                now = time.time()
                conn.execute(
                    "UPDATE api_keys SET revoked_at = ?, prev_hash = NULL,"
                    " prev_prefix = NULL, prev_expires_at = NULL WHERE id = ?",
                    (now, key_id),
                )
                audit(
                    conn,
                    principal.actor,
                    "key.revoked",
                    actor_id=principal.actor_id,
                    target=key_id,
                    meta={"name": row["name"], "prefix": row["prefix"]},
                )
            conn.commit()
        finally:
            conn.close()
        return json_response({"id": key_id, "revoked": True})

    def _rotate(request: Request, key_id: str) -> Response:
        conn = connect(db_path)
        try:
            principal = gate(
                request,
                conn,
                config,
                db_path,
                "keys.write",
                action="key.rotated",
                target=key_id,
            )
            row = _load(conn, key_id)
            if row["revoked_at"] is not None:
                raise HttpError(400, "invalid_request", "cannot rotate a revoked key")
            secret = generate_secret()
            now = time.time()
            conn.execute(
                "UPDATE api_keys SET prev_hash = hash, prev_prefix = prefix,"
                " prev_expires_at = ?, hash = ?, prefix = ? WHERE id = ?",
                (
                    now + GRACE_SECONDS,
                    hash_secret(secret, load_pepper(Path(db_path).parent)),
                    prefix_of(secret),
                    key_id,
                ),
            )
            audit(
                conn,
                principal.actor,
                "key.rotated",
                actor_id=principal.actor_id,
                target=key_id,
                meta={
                    "prefix": prefix_of(secret),
                    "grace_seconds": int(GRACE_SECONDS),
                },
            )
            conn.commit()
        finally:
            conn.close()
        return json_response(
            {
                "id": key_id,
                "key": secret,
                "prefix": prefix_of(secret),
                "grace": int(GRACE_SECONDS),
                "note": _NOTE_ONCE,
            }
        )

    def _set_limits(request: Request, key_id: str) -> Response:
        payload = _body(request)
        unknown = [field for field in payload if field not in _LIMIT_FIELDS]
        if unknown:
            raise _bad(unknown[0], f"unknown field {unknown[0]!r}")
        if not any(field in payload for field in _LIMIT_FIELDS):
            raise _bad("rate_limit_per_min", "no limit fields to update")
        limits = _limits(payload)
        conn = connect(db_path)
        try:
            principal = gate(
                request,
                conn,
                config,
                db_path,
                "keys.write",
                action="ratelimit.updated",
                target=f"key:{key_id}",
            )
            row = _load(conn, key_id)
            before = {
                "rate_limit_per_min": row["rate_limit_per_min"],
                "burst": row["burst"],
            }
            after = {
                "rate_limit_per_min": limits["rate_limit_per_min"],
                "burst": limits["burst"],
            }
            conn.execute(
                "UPDATE api_keys SET rate_limit_per_min = ?, burst = ? WHERE id = ?",
                (after["rate_limit_per_min"], after["burst"], key_id),
            )
            audit(
                conn,
                principal.actor,
                "ratelimit.updated",
                actor_id=principal.actor_id,
                target=f"key:{key_id}",
                meta={"before": before, "after": after},
            )
            conn.commit()
        finally:
            conn.close()
        return json_response({"id": key_id, **after})

    router.add("GET", "/api/v1/keys", list_keys)
    router.add("POST", "/api/v1/keys", create_key)
    router.add("DELETE", "/api/v1/keys", delete_by_body)
    router.add("DELETE", "/api/v1/keys/*", delete_by_path)
    router.add("POST", "/api/v1/keys/*", post_wildcard)
    router.add("PATCH", "/api/v1/keys/*", patch_key)
    router.add("GET", "/api/v1/keys/auth", get_auth_state)
    router.add("POST", "/api/v1/keys/auth", set_auth_state)


def _load(conn: sqlite3.Connection, key_id: str) -> dict:
    row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    if row is None:
        raise HttpError(404, "not_found", f"key {key_id} not found")
    return dict(row)


def _has_active_key(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM api_keys WHERE revoked_at IS NULL LIMIT 1").fetchone()
        is not None
    )


def _key_payload(row: sqlite3.Row | dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "created": row["created_at"],
        "last_used": row["last_used"],
        "request_count": row["request_count"],
        "revoked": row["revoked_at"] is not None,
        "rate_limit_per_min": row["rate_limit_per_min"],
        "burst": row["burst"],
    }


def _limits(payload: dict[str, Any]) -> dict[str, int | None]:
    """Validate the optional per-key limit fields; absent fields mean "leave unchanged"
    on PATCH and "use the global default" (null) on create."""
    out: dict[str, int | None] = {}
    for field in _LIMIT_FIELDS:
        if field not in payload:
            out[field] = None
            continue
        value = payload[field]
        if value is None:
            out[field] = None
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _bad(field, f"'{field}' must be a non-negative integer or null")
        else:
            out[field] = value
    return out


def _reject_unknown_query(request: Request) -> None:
    unknown = next(iter(request.query), None)
    if unknown is not None:
        raise HttpError(400, "invalid_filter", f"unknown query parameter {unknown!r}")


def _body(request: Request) -> dict:
    payload = request.json()
    if not isinstance(payload, dict):
        raise HttpError(400, "invalid_request", "request body must be a JSON object")
    return payload


def _bad(field: str, message: str) -> HttpError:
    return HttpError(400, "invalid_request", message, details={"field": field})


def _unmatched(request: Request) -> HttpError:
    return HttpError(404, "not_found", f"no route for {request.method} {request.path}")
