"""Log read endpoints (docs/api-reference.md section 7).

``GET /api/v1/logs`` tails the log ring through ``store.queries.logs_list``: allowlisted
filters (``level``, ``q``, ``trace_id``, ``range``, ``since``/``until``, ``limit``,
``cursor``) and the keyset cursor are enforced by the query layer.
``GET /api/v1/logs/export`` downloads up to 10 000 newest matching lines as a
``text/plain`` attachment with one JSON object per line; the window is resolved once so
every page reads the same slice, and an empty result is an empty 200 body.

Authentication and role gates arrive in Phase 3: the documented owner/admin export gate
is not enforced yet.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from layawatch.http.types import HttpError, Request, Response, json_response, text_response
from layawatch.store.db import connect
from layawatch.store.queries import MAX_LIMIT, InvalidFilter, logs_list, parse_range

if TYPE_CHECKING:
    from layawatch.http.router import Router

#: Export query parameters; ``format`` accepts only ``jsonl``.
_EXPORT_PARAMS = frozenset({"range", "format"})
#: Hard cap on exported lines, paged through ``logs_list`` at ``MAX_LIMIT`` per request.
_EXPORT_LIMIT = 10_000


def add_logs_routes(router: Router, db_path: str | Path) -> None:
    """Register ``GET /api/v1/logs`` and ``GET /api/v1/logs/export`` on ``router``."""

    def list_logs(request: Request) -> Response:
        try:
            conn = connect(db_path)
            try:
                payload = logs_list(conn, request.query)
            finally:
                conn.close()
        except InvalidFilter as exc:
            raise HttpError(400, "invalid_filter", str(exc)) from None
        return json_response(payload)

    def export(request: Request) -> Response:
        try:
            for key in request.query:
                if key not in _EXPORT_PARAMS:
                    raise InvalidFilter(f"unknown query parameter {key!r}")
            if request.param("format") != "jsonl":
                raise InvalidFilter("format must be jsonl")
            raw_range = request.param("range")
            token = "1h" if raw_range in (None, "") else raw_range
            range_s = parse_range(token)
            now = time.time()
            window = {"since": str(now - range_s), "until": str(now), "limit": str(MAX_LIMIT)}
            items: list[dict] = []
            conn = connect(db_path)
            try:
                cursor: str | None = None
                while len(items) < _EXPORT_LIMIT:
                    params = dict(window)
                    if cursor is not None:
                        params["cursor"] = cursor
                    page = logs_list(conn, params)
                    items.extend(page["items"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
            finally:
                conn.close()
        except InvalidFilter as exc:
            raise HttpError(400, "invalid_filter", str(exc)) from None
        body = "".join(
            json.dumps(item, separators=(",", ":")) + "\n" for item in items[:_EXPORT_LIMIT]
        )
        return text_response(
            body,
            headers={"Content-Disposition": f"attachment; filename=logs-{token}.jsonl"},
        )

    router.add("GET", "/api/v1/logs", list_logs)
    router.add("GET", "/api/v1/logs/export", export)
