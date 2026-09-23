"""Settings read view and runtime write path (docs/api-reference.md section 3).

``GET /api/v1/settings`` merges the safe config snapshot with the runtime ``settings``
table (raw TEXT values keyed by row key) so the Settings page shows effective values in
one payload: ``config`` is the env-driven view, ``settings`` the raw rows that override it
at runtime, and ``effective`` the typed, merged value of each runtime-editable control -
the settings row when written, the config default otherwise - always present, so a
control never depends on a row having existed. The settings table must never hold
secrets; as defence in depth a row whose key looks secret-like (contains ``secret``,
``token`` or ``password``) is omitted from the response. The read view stays on the
phase-3 credential-less boundary the contract tests pin.

``POST /api/v1/settings`` is the acceptance-9 write path: a partial update of the
runtime-editable controls, admin+ through ``settings.write``, with ``capture_payloads``
additionally requiring the owner-only ``settings.capture`` permission. Every field is
validated (ints with their bounds, the sample fraction between 0 and 1, capture a
boolean), stored with ``updated_at``/``updated_by``, and audited ``settings.updated`` in
the same transaction with before and after values - plus ``payload_capture.toggled`` when
the capture switch moves. Rows live in the existing ``settings`` table, so a GET reflects
every write immediately and across restarts (rollback note, plan phase 6).
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.api.health import safe_config_snapshot
from layawatch.auth.audit import audit
from layawatch.auth.sessions import gate
from layawatch.auth.users import require_permission
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.config import Config
    from layawatch.http.router import Router

#: Key fragments that mark a settings row as secret-like; such rows never leave the server.
_SECRET_KEY_FRAGMENTS = ("secret", "token", "password")

#: Read-view note: precedence of the two sources and the write gate (POST landed).
_NOTE = "runtime settings override config where present; writes gated by settings.write (admin+)"

#: Values a stored TEXT row accepts as true (mirrors api/auth.py's settings readers).
_TRUE_TEXT = frozenset({"1", "true", "yes", "on"})

#: The runtime-editable controls (acceptance 9, plan task 14), in ``effective`` order.
_CONTROLS = (
    "retention_traces",
    "retention_days",
    "log_ring_size",
    "trace_sample",
    "stream_tick",
    "capture_payloads",
)
_CONTROL_SET = frozenset(_CONTROLS)

#: Integer floors; ``log_ring_size`` mirrors the config loader's validation.
_INT_MINIMUMS = {"retention_traces": 1, "retention_days": 1, "log_ring_size": 100, "stream_tick": 1}


def add_settings_routes(router: Router, config: Config, db_path: str | Path) -> None:
    """Register ``GET`` and ``POST`` /api/v1/settings (api-reference section 3)."""

    def read(request: Request) -> Response:
        _reject_unknown_query(request)
        conn = connect(db_path)
        try:
            return json_response(_payload(conn, config))
        finally:
            conn.close()

    def update(request: Request) -> Response:
        _reject_unknown_query(request)
        payload = request.json()
        if not isinstance(payload, dict):
            raise _bad("body", "request body must be a JSON object")
        if not payload:
            raise _bad("body", "no settings to update")
        stored: dict[str, str] = {}
        for name, value in payload.items():
            if name not in _CONTROL_SET:
                raise _bad(name, f"unknown setting {name!r}")
            stored[name] = _control_text(name, value)
        conn = connect(db_path)
        try:
            principal = gate(request, conn, db_path, "settings.write", action="settings.updated")
            if "capture_payloads" in stored:
                require_permission(
                    conn, principal, "settings.capture", action="payload_capture.toggled"
                )
            existing = _rows(conn)
            before = {
                name: _effective_value(config, existing, name) for name in sorted(stored)
            }
            after = {name: _typed(name, text) for name, text in sorted(stored.items())}
            now = time.time()
            for name, text in stored.items():
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at, updated_by)"
                    " VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET"
                    " value = excluded.value, updated_at = excluded.updated_at,"
                    " updated_by = excluded.updated_by",
                    (name, text, now, principal.actor),
                )
            audit(
                conn,
                principal.actor,
                "settings.updated",
                actor_id=principal.actor_id,
                target=",".join(sorted(stored)),
                meta={"before": before, "after": after},
            )
            if "capture_payloads" in stored:
                audit(
                    conn,
                    principal.actor,
                    "payload_capture.toggled",
                    actor_id=principal.actor_id,
                    target="capture_payloads",
                    meta={
                        "before": before["capture_payloads"],
                        "after": after["capture_payloads"],
                    },
                )
            conn.commit()
            response = _payload(conn, config)
        finally:
            conn.close()
        return json_response(response)

    router.add("GET", "/api/v1/settings", read)
    router.add("POST", "/api/v1/settings", update)


def _payload(conn, config: Config) -> dict:
    rows = _rows(conn)
    overrides = {row["key"]: row["value"] for row in rows if not _secret_key(row["key"])}
    return {
        "config": safe_config_snapshot(config),
        "settings": overrides,
        "effective": {
            name: _effective_value(config, rows, name) for name in _CONTROLS
        },
        "note": _NOTE,
    }


def _rows(conn) -> list:
    return conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()


def _effective_value(config: Config, rows: list, name: str) -> Any:
    """Typed value of one control: the settings row when valid, else the config default."""
    default = getattr(config, name)
    text = next((row["value"] for row in rows if row["key"] == name), None)
    if text is None:
        return default
    try:
        return _typed(name, text)
    except (TypeError, ValueError):
        return default  # hand-edited row: the env default is the safer answer for a read


def _typed(name: str, text: str) -> Any:
    """Parse a canonical stored TEXT row back to its typed value (raises on garbage)."""
    if name == "capture_payloads":
        return text.strip().lower() in _TRUE_TEXT
    if name == "trace_sample":
        return float(text)
    return int(text)


def _control_text(name: str, value: Any) -> str:
    """Validate one control from a POST body; returns the canonical TEXT to store."""
    if name == "capture_payloads":
        if not isinstance(value, bool):
            raise _bad(name, "'capture_payloads' must be a boolean")
        return "1" if value else "0"
    if name == "trace_sample":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _bad(name, "'trace_sample' must be a number between 0 and 1")
        if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
            raise _bad(name, "'trace_sample' must be between 0 and 1")
        return str(float(value))
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad(name, f"'{name}' must be an integer")
    minimum = _INT_MINIMUMS[name]
    if value < minimum:
        raise _bad(name, f"'{name}' must be at least {minimum}")
    return str(value)


def _reject_unknown_query(request: Request) -> None:
    unknown = next(iter(request.query), None)
    if unknown is not None:
        raise HttpError(400, "invalid_filter", f"unknown query parameter {unknown!r}")


def _secret_key(key: str) -> bool:
    """True when ``key`` contains a secret-bearing fragment (case-insensitive)."""
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)


def _bad(field: str, message: str) -> HttpError:
    return HttpError(400, "invalid_request", message, details={"field": field})
