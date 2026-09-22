"""Same-transaction audit helper (plan phase-3 task 11, docs/security.md 5).

``audit`` inserts an ``audit_log`` row inside the caller's transaction without committing,
so an action cannot succeed without its record: rolling back the caller rolls back the
audit row too. Role-check denials are the exception -- their helper commits before raising
so the ``result = denied`` row survives the 403 that follows.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Mapping


def audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    *,
    actor_id: str | None = None,
    target: str | None = None,
    result: str = "ok",
    meta: Mapping[str, Any] | None = None,
) -> None:
    """Append one audit row in the caller's transaction; no commit (the caller owns it)."""
    conn.execute(
        "INSERT INTO audit_log (ts, actor_id, actor, action, target, result, meta)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            time.time(),
            actor_id,
            str(actor),
            action,
            target,
            result,
            json.dumps(dict(meta)) if meta is not None else None,
        ),
    )
