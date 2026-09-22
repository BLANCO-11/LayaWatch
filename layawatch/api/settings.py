"""Settings read view (docs/api-reference.md section 3).

``GET /api/v1/settings`` merges the safe config snapshot with the runtime ``settings``
table (raw TEXT values keyed by row key) so the Settings page shows effective values in
one payload: ``config`` is the env-driven view, ``settings`` the rows that override it at
runtime. The settings table must never hold secrets; as defence in depth a row whose key
looks secret-like (contains ``secret``, ``token`` or ``password``) is omitted from the
response.

Authentication and role gates arrive in Phase 3: the read view works without credentials
and the write path (``POST /api/v1/settings``) lands with them.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from layawatch.api.health import safe_config_snapshot
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.config import Config
    from layawatch.http.router import Router

#: Key fragments that mark a settings row as secret-like; such rows never leave the server.
_SECRET_KEY_FRAGMENTS = ("secret", "token", "password")

#: Read-view note: precedence of the two sources and the Phase 3 write boundary.
_NOTE = "runtime settings override config where present; write path lands with roles (Phase 3)"


def add_settings_routes(router: Router, config: Config, db_path: str | Path) -> None:
    """Register ``GET /api/v1/settings``: safe config, runtime rows and the phase note."""

    def settings(request: Request) -> Response:
        unknown = next(iter(request.query), None)
        if unknown is not None:
            raise HttpError(400, "invalid_filter", f"unknown query parameter {unknown!r}")
        conn = connect(db_path)
        try:
            rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        finally:
            conn.close()
        overrides = {row["key"]: row["value"] for row in rows if not _secret_key(row["key"])}
        return json_response(
            {"config": safe_config_snapshot(config), "settings": overrides, "note": _NOTE}
        )

    router.add("GET", "/api/v1/settings", settings)


def _secret_key(key: str) -> bool:
    """True when ``key`` contains a secret-bearing fragment (case-insensitive)."""
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)
