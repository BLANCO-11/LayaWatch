"""Audit log read surface (docs/api-reference.md section 13, plan phase-6 task 11).

``GET /api/v1/audit`` is admin+ (``audit.read``), answers the standard pagination
envelope - ``{items, next_cursor, total_estimate}``, newest first, keyset cursor pages
older rows exactly like the trace and log lists - and never mutates the append-only
table. Filters: ``actor`` (case-insensitive substring), ``action`` (exact), and the
section 2 time window (``since``/``until`` epoch seconds or ``range`` shorthand).
Unknown query parameters are ``400 invalid_filter``, never ignored (section 2).
``meta`` leaves the JSON column already parsed.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.auth.sessions import gate
from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store import queries
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.http.router import Router

#: Section 13 query contract; anything else is a 400 invalid_filter.
_PARAMS = frozenset({"actor", "action", "since", "until", "range", "limit", "cursor"})

_COLUMNS = "id, ts, actor_id, actor, action, target, result, meta"


def add_audit_routes(router: Router, *, db_path: str | Path) -> None:
    """Register ``GET /api/v1/audit`` on ``router``."""

    def list_audit(request: Request) -> Response:
        for key in request.query:
            if key not in _PARAMS:
                raise HttpError(400, "invalid_filter", f"unknown query parameter {key!r}")
        try:
            since, until = queries.resolve_window(request.query)
            limit = queries.parse_limit(_first(request.query, "limit"))
        except queries.InvalidFilter as exc:
            raise HttpError(400, "invalid_filter", str(exc)) from None

        clauses: list[str] = []
        args: list[Any] = []
        if since is not None:
            clauses.append("ts >= ?")
            args.append(since)
        if until is not None:
            clauses.append("ts < ?")
            args.append(until)
        actor = _opt(request.query, "actor")
        if actor is not None:
            clauses.append("actor LIKE ? ESCAPE '\\'")
            args.append(f"%{_like(actor)}%")
        action = _opt(request.query, "action")
        if action is not None:
            clauses.append("action = ?")
            args.append(action)
        filter_sql = " AND ".join(clauses) if clauses else "1"
        filter_args = list(args)

        cursor_raw = _opt(request.query, "cursor")
        if cursor_raw is not None:
            ts, row_id = _decode_cursor(cursor_raw)
            clauses.append("(ts < ? OR (ts = ? AND id < ?))")
            args.extend((ts, ts, row_id))
        where_sql = " AND ".join(clauses) if clauses else "1"

        conn = connect(db_path)
        try:
            gate(request, conn, db_path, "audit.read")
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM audit_log WHERE {where_sql}"
                " ORDER BY ts DESC, id DESC LIMIT ?",
                [*args, limit + 1],
            ).fetchall()
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM audit_log WHERE {filter_sql}", filter_args
                ).fetchone()[0]
            )
        finally:
            conn.close()

        has_more = len(rows) > limit
        page = rows[:limit]
        items = []
        for row in page:
            item = dict(row)
            item["meta"] = _decode_meta(item["meta"])
            items.append(item)
        next_cursor = None
        if has_more and page:
            next_cursor = _encode_cursor({"k": [page[-1]["ts"], page[-1]["id"]]})
        return json_response(
            {"items": items, "next_cursor": next_cursor, "total_estimate": total}
        )

    router.add("GET", "/api/v1/audit", list_audit)


def _first(params: dict[str, list[str]], name: str) -> Any:
    values = params.get(name)
    return values[0] if values else None


def _opt(params: dict[str, list[str]], name: str) -> str | None:
    value = _first(params, name)
    return None if value is None or value == "" else value


def _like(raw: str) -> str:
    """Escape LIKE metacharacters so the actor filter matches literally (queries._like)."""
    return (
        raw.lower()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _decode_meta(raw: Any) -> Any:
    """Parse the JSON meta column; a malformed value degrades to null, never 500."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(raw: str) -> tuple[float, int]:
    """``(ts, id)`` from a keyset cursor; a malformed cursor is 400 invalid_filter."""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        keys = payload["k"]
        ts, row_id = keys[0], keys[1]
        if not isinstance(ts, (int, float)) or not isinstance(row_id, int):
            raise ValueError("bad key types")
    except (ValueError, KeyError, TypeError):
        raise HttpError(400, "invalid_filter", "cursor is not a valid pagination cursor") from None
    return float(ts), int(row_id)
