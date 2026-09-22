"""User CRUD, the shared permission table, and the security.md 2 invariants (plan task 4).

``PERMISSIONS`` maps every gated action in ``docs/security.md`` section 2 to its minimum
role; it is the single table phase-3 handlers and the later middleware role gate share
(invariant 4: no handler checks roles ad hoc). ``require_permission`` is the one role
check: a failure commits an audit row with ``result = denied`` before raising ``403``
(invariant 3).

The invariants live in the mutation helpers: the last enabled owner can never be deleted,
demoted or disabled; a user cannot change their own role or delete themselves. Role
changes and disables revoke the target's sessions, so the session id observed at login
changes after a privilege change (acceptance criterion 11).
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import time
from typing import Any

from layawatch.auth.audit import audit
from layawatch.http.types import HttpError

#: Roles ordered weakest to strongest (api-reference section 10).
ROLES = ("viewer", "admin", "owner")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}

#: One shared permission table: action -> minimum role (docs/security.md section 2).
PERMISSIONS: dict[str, str] = {
    # Reads available to every authenticated role.
    "traces.read": "viewer",
    "metrics.read": "viewer",
    "logs.read": "viewer",
    "models.read": "viewer",
    "keys.list": "viewer",
    "keys.read_auth": "viewer",
    "settings.read": "viewer",
    "auth.me": "viewer",
    # Admin surface: mutations outside user management.
    "traces.write": "admin",
    "traces.delete": "admin",
    "logs.export": "admin",
    "models.write": "admin",
    "keys.write": "admin",
    "settings.write": "admin",
    "audit.read": "admin",
    "playground.run": "admin",
    # Owner surface: identity and sensitive toggles.
    "users.read": "admin",
    "users.write": "owner",
    "sessions.read": "owner",
    "sessions.revoke_all": "owner",
    "settings.capture": "owner",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def required_role(permission: str) -> str:
    """Minimum role for ``permission``; unknown permissions fail closed to owner."""
    return PERMISSIONS.get(permission, "owner")


def role_allows(role: str, permission: str) -> bool:
    """True when ``role`` ranks at least as high as the permission's requirement."""
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[required_role(permission)]


def permissions_for(role: str) -> list[str]:
    """Every permission name the role holds (the ``GET /auth/me`` ``permissions`` list)."""
    return sorted(name for name in PERMISSIONS if role_allows(role, name))


def require_permission(
    conn: sqlite3.Connection,
    principal: Any,
    permission: str,
    *,
    action: str | None = None,
    target: str | None = None,
) -> None:
    """Enforce ``permission`` for ``principal``; a failure writes the denied audit row in
    a committed transaction and raises 403 (security.md invariant 3). Mutating endpoints
    pass their documented write action; read-only gates fall back to the permission name
    so no invented action family is introduced."""
    needed = required_role(permission)
    if role_allows(principal.role, permission):
        return
    audit(
        conn,
        principal.actor,
        action or permission,
        actor_id=principal.actor_id,
        target=target,
        result="denied",
        meta={
            "permission": permission,
            "required_role": needed,
            "role": principal.role,
        },
    )
    conn.commit()  # the denied row must survive the 403 the raise triggers
    raise HttpError(
        403,
        "forbidden",
        f"{permission} requires the {needed} role",
        details={"required_role": needed, "role": principal.role},
    )


def normalize_email(email: Any) -> str:
    """Lowercased, trimmed email; raises 400 when the shape is not an email address."""
    if not isinstance(email, str) or not email.strip():
        raise _bad("email", "email is required")
    cleaned = email.strip().lower()
    if len(cleaned) > 254 or not _EMAIL_RE.match(cleaned):
        raise _bad("email", "email must look like an email address")
    return cleaned


def user_payload(row: sqlite3.Row | dict) -> dict:
    """Public user shape (everything except the password hash)."""
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        "disabled": bool(row["disabled"]),
        "must_change": bool(row["must_change"]),
    }


def insert_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    name: str,
    role: str,
    password_hash: str,
    must_change: int = 0,
) -> dict:
    """Insert one user and return its public payload; 409 on a duplicate email."""
    user_id = secrets.token_hex(8)
    created = time.time()
    try:
        conn.execute(
            "INSERT INTO users (id, email, name, role, password_hash, created_at,"
            " disabled, must_change) VALUES (?,?,?,?,?,?,0,?)",
            (user_id, email, name, role, password_hash, created, int(must_change)),
        )
    except sqlite3.IntegrityError:
        raise HttpError(409, "email_taken", "a user with that email already exists") from None
    return {
        "id": user_id,
        "email": email,
        "name": name,
        "role": role,
        "created_at": created,
        "last_login_at": None,
        "disabled": False,
        "must_change": bool(must_change),
    }


def fetch_user(conn: sqlite3.Connection, user_id: str) -> dict | None:
    """One user row as a dict (including the hash for password checks), or None."""
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row is not None else None


def enabled_owner_count(conn: sqlite3.Connection, excluding: str | None = None) -> int:
    """Number of enabled owners, optionally ignoring ``excluding`` (the target row)."""
    if excluding is None:
        row = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'owner' AND disabled = 0"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'owner' AND disabled = 0 AND id != ?",
            (excluding,),
        ).fetchone()
    return int(row[0])


def apply_user_update(
    conn: sqlite3.Connection,
    principal: Any,
    target: dict,
    payload: dict,
) -> dict:
    """Apply a validated PATCH under the security.md 2 invariants; audits the changes.

    Raises 403 (with the ``denied`` audit row) when the change would demote or disable
    the last enabled owner, change the actor's own role, or when new field values are
    invalid (400). Role changes and disables revoke the target's sessions so privilege
    changes rotate every session id the target holds.
    """
    changed: dict[str, Any] = {}
    for field in ("name", "role", "disabled", "must_change"):
        if field in payload:
            changed[field] = payload[field]

    if "role" in changed and changed["role"] != target["role"]:
        _guard_role_change(conn, principal, target)
    if changed.get("disabled") is True and not int(target["disabled"]):
        _guard_last_owner(conn, principal, target, action="user.disabled")

    role_changed = "role" in changed and changed["role"] != target["role"]
    disabled_changed = "disabled" in changed and bool(changed["disabled"]) != bool(
        target["disabled"]
    )

    assignments = []
    values: list[Any] = []
    for field, value in changed.items():
        assignments.append(f"{field} = ?")
        values.append(int(value) if field in ("disabled", "must_change") else value)
    if assignments:
        values.append(target["id"])
        conn.execute(f"UPDATE users SET {', '.join(assignments)} WHERE id = ?", values)

    if role_changed:
        audit(
            conn,
            principal.actor,
            "user.role_changed",
            actor_id=principal.actor_id,
            target=target["id"],
            meta={"from": target["role"], "to": changed["role"], "email": target["email"]},
        )
        conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
            (target["id"],),
        )
    if disabled_changed:
        audit(
            conn,
            principal.actor,
            "user.disabled",
            actor_id=principal.actor_id,
            target=target["id"],
            meta={"disabled": bool(changed["disabled"]), "email": target["email"]},
        )
        if changed["disabled"]:
            conn.execute(
                "UPDATE sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (target["id"],),
            )
    updated = fetch_user(conn, target["id"])
    assert updated is not None  # the row was loaded in the same transaction
    return user_payload(updated)


def delete_user(conn: sqlite3.Connection, principal: Any, target: dict) -> None:
    """Delete a user under the security.md 2 invariants; audits ``user.deleted``."""
    if principal.actor_id == target["id"]:
        _denied(conn, principal, "user.deleted", target["id"], "users cannot delete themselves")
    _guard_last_owner(conn, principal, target, action="user.deleted")
    conn.execute("DELETE FROM users WHERE id = ?", (target["id"],))
    audit(
        conn,
        principal.actor,
        "user.deleted",
        actor_id=principal.actor_id,
        target=target["id"],
        meta={"email": target["email"], "role": target["role"]},
    )


def _guard_role_change(conn: sqlite3.Connection, principal: Any, target: dict) -> None:
    if principal.actor_id == target["id"]:
        _denied(
            conn,
            principal,
            "user.role_changed",
            target["id"],
            "users cannot change their own role",
        )
    if target["role"] == "owner" and not int(target["disabled"]):
        _guard_last_owner(conn, principal, target, action="user.role_changed")


def _guard_last_owner(conn: sqlite3.Connection, principal: Any, target: dict, *, action: str) -> None:
    if (
        target["role"] == "owner"
        and not int(target["disabled"])
        and enabled_owner_count(conn, excluding=target["id"]) == 0
    ):
        _denied(
            conn,
            principal,
            action,
            target["id"],
            "the last enabled owner can never be deleted, demoted or disabled",
        )


def _denied(conn: sqlite3.Connection, principal: Any, action: str, target: str, reason: str) -> None:
    audit(
        conn,
        principal.actor,
        action,
        actor_id=principal.actor_id,
        target=target,
        result="denied",
        meta={"reason": reason, "role": principal.role},
    )
    conn.commit()  # the denied row must survive the 403 the raise triggers
    raise HttpError(403, "forbidden", reason)


def _bad(field: str, message: str) -> HttpError:
    return HttpError(400, "invalid_request", message, details={"field": field})
