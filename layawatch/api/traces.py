"""Trace endpoints: list, detail, observations, scores, tags and delete (api-reference 5),
plus the phase-6 D-013 danger-zone range delete.

Handlers follow the middleware ``_verify_auth`` connection pattern: a short-lived SQLite
connection per request, closed in ``finally``. Allowlisted filters, keyset pagination,
waterfall ordering and the summary math live in ``store.queries``; this module maps HTTP
onto those calls and renders the shared error envelope. Authentication and role gating
arrive in Phase 3 (plan/phase-2-read-api), so these handlers accept credential-less
requests today. The one exception is ``add_range_delete_route`` (plan phase-6 D-013):
bulk deletion is admin+ through the shared ``traces.delete`` permission and audited with
its range and count; it registers from its own ``add_*`` line because it needs the
``Config`` the six-phase-5 endpoints never take.
"""
from __future__ import annotations

import math
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.auth.audit import audit
from layawatch.auth.sessions import gate
from layawatch.http.types import HttpError, Request, Response, empty_response, json_response
from layawatch.obs.recorder import Score
from layawatch.store import queries
from layawatch.store.db import connect

if TYPE_CHECKING:
    from layawatch.http.router import Router

#: Time-window parameters the D-013 range delete accepts (section 2 shorthand included).
_RANGE_PARAMS = frozenset({"since", "until", "range"})

_PREFIX = "/api/v1/traces/"
#: Observation types the waterfall endpoint returns: span and generation rows. ``event``
#: rows are instantaneous markers, not span-shaped work, so they stay detail-only.
_WATERFALL_TYPES = frozenset({"span", "generation"})
#: Trace tag limits (obs-model 3): free-form labels, max 10, each <= 32 characters.
_MAX_TAGS = 10
_MAX_TAG_LEN = 32
#: Score comment cap; longer comments are rejected, not truncated (api-reference 5).
_MAX_COMMENT = 500
_DATA_TYPES = frozenset({"numeric", "boolean", "categorical"})
#: The score source this endpoint accepts and records (obs-model 8: v0.1 sources).
_SOURCE = "api"


def add_trace_routes(router: Router, db_path: str | Path) -> None:
    """Register the six trace endpoints of api-reference section 5.

    ``GET /api/v1/traces`` is an exact route; the other five hang off one wildcard per
    method (the router knows exact paths and trailing-``*`` prefixes only), so the
    dispatchers split the segments after the prefix: a bare ``{id}`` is the detail (or,
    for DELETE, the delete), ``{id}/observations`` the waterfall page, and for POST the
    final segment picks ``scores`` or ``tags``. Exact beats prefix and the longest
    prefix wins, so the list route never collides with the wildcard.
    """

    def list_route(request: Request) -> Response:
        conn = connect(db_path)
        try:
            try:
                page = queries.list_traces(conn, request.query)
            except queries.InvalidFilter as exc:
                raise HttpError(400, "invalid_filter", str(exc)) from None
        finally:
            conn.close()
        return json_response(page)

    def get_route(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 1:
            return _detail(parts[0])
        if len(parts) == 2 and parts[1] == "observations":
            return _observations(parts[0])
        raise _unmatched(request)

    def post_route(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 2:
            trace_id, action = parts
            if action == "scores":
                return _scores(trace_id, request)
            if action == "tags":
                return _tags(trace_id, request)
        raise _unmatched(request)

    def delete_route(request: Request) -> Response:
        parts = request.path[len(_PREFIX) :].split("/")
        if len(parts) == 1:
            return _delete(parts[0])
        raise _unmatched(request)

    def _detail(trace_id: str) -> Response:
        conn = connect(db_path)
        try:
            detail = queries.trace_detail(conn, trace_id)
        finally:
            conn.close()
        if detail is None:
            raise _unknown(trace_id)
        return json_response(detail)

    def _observations(trace_id: str) -> Response:
        conn = connect(db_path)
        try:
            detail = queries.trace_detail(conn, trace_id)
        finally:
            conn.close()
        if detail is None:
            raise _unknown(trace_id)
        items = [
            observation
            for observation in detail["observations"]
            if observation["type"] in _WATERFALL_TYPES
        ]
        return json_response({"items": items})

    def _scores(trace_id: str, request: Request) -> Response:
        name, value, data_type, comment = _validated_score(request)
        conn = connect(db_path)
        try:
            if queries.trace_detail(conn, trace_id) is None:
                raise _unknown(trace_id)
            score = Score(
                id=secrets.token_hex(8),
                trace_id=trace_id,
                name=name,
                value=value,
                data_type=data_type,
                source=_SOURCE,
                comment=comment,
                ts=time.time(),
            )
            queries.insert_scores(conn, [score])
            queries.insert_audit(conn, "api", "trace.scored", target=trace_id)
            conn.commit()
        finally:
            conn.close()
        return json_response(
            {
                "id": score.id,
                "name": score.name,
                "value": score.value,
                "data_type": score.data_type,
                "source": score.source,
                "comment": score.comment,
                "ts": score.ts,
            },
            status=201,
        )

    def _tags(trace_id: str, request: Request) -> Response:
        add, remove = _validated_tags(request)
        conn = connect(db_path)
        try:
            detail = queries.trace_detail(conn, trace_id)
            if detail is None:
                raise _unknown(trace_id)
            stored = detail["trace"]["tags"]
            tags = [tag for tag in stored if tag not in remove] if isinstance(stored, list) else []
            for tag in add:
                if tag not in tags:
                    tags.append(tag)
            if len(tags) > _MAX_TAGS:
                raise HttpError(
                    400,
                    "invalid_request",
                    f"a trace may carry at most {_MAX_TAGS} tags",
                    details={"field": "tags"},
                )
            queries.set_trace_tags(conn, trace_id, tags)
            queries.insert_audit(conn, "api", "trace.tagged", target=trace_id)
            conn.commit()
        finally:
            conn.close()
        return json_response({"tags": tags})

    def _delete(trace_id: str) -> Response:
        conn = connect(db_path)
        try:
            if not queries.delete_trace(conn, trace_id):
                raise _unknown(trace_id)
            queries.insert_audit(
                conn, "api", "trace.deleted", target=trace_id, meta={"id": trace_id}
            )
            conn.commit()
        finally:
            conn.close()
        return empty_response(204)

    router.add("GET", "/api/v1/traces", list_route)
    router.add("GET", "/api/v1/traces/*", get_route)
    router.add("POST", "/api/v1/traces/*", post_route)
    router.add("DELETE", "/api/v1/traces/*", delete_route)


def add_range_delete_route(router: Router, db_path: str | Path) -> None:
    """Register ``DELETE /api/v1/traces?since=&until=`` (plan phase-6 D-013).

    Danger-zone bulk delete: at least one time bound is required (an unbounded delete is
    a 400, never an accident), ``since`` inclusive and ``until`` exclusive exactly like
    the list window, admin+ through ``traces.delete`` (security.md 2), and one bounded
    transaction whose audit row (``trace.deleted``, same-transaction) carries the range
    and the deleted count. Deletion semantics stay those of ``queries.delete_trace`` and
    the retention prune: a set-based ``DELETE FROM traces`` cascades observations and
    scores through the foreign keys and keeps ``log_entry`` rows (no foreign key), and
    the metric rollups - independent rows the retention pass only prunes past
    ``rollup_days`` - survive so metrics history outlives the raw traces.
    """

    def delete_range(request: Request) -> Response:
        for key in request.query:
            if key not in _RANGE_PARAMS:
                raise HttpError(400, "invalid_filter", f"unknown query parameter {key!r}")
        try:
            since, until = queries.resolve_window(request.query)
        except queries.InvalidFilter as exc:
            raise HttpError(400, "invalid_filter", str(exc)) from None
        if since is None and until is None:
            raise HttpError(
                400, "invalid_filter", "a time range is required: pass since, until or range"
            )
        if since is not None and until is not None and since >= until:
            raise HttpError(400, "invalid_filter", "since must be before until")

        clauses: list[str] = []
        args: list[Any] = []
        if since is not None:
            clauses.append("ts_start >= ?")
            args.append(since)
        if until is not None:
            clauses.append("ts_start < ?")
            args.append(until)
        where_sql = " AND ".join(clauses)

        conn = connect(db_path)
        try:
            principal = gate(request, conn, db_path, "traces.delete", action="trace.deleted")
            deleted = conn.execute(
                f"DELETE FROM traces WHERE {where_sql}", args
            ).rowcount
            audit(
                conn,
                principal.actor,
                "trace.deleted",
                actor_id=principal.actor_id,
                meta={"since": since, "until": until, "count": deleted},
            )
            conn.commit()
        finally:
            conn.close()
        return json_response({"deleted": deleted, "since": since, "until": until})

    router.add("DELETE", "/api/v1/traces", delete_range)


def _unknown(trace_id: str) -> HttpError:
    return HttpError(404, "not_found", f"trace {trace_id} not found")


def _unmatched(request: Request) -> HttpError:
    """Same envelope the router renders for a path no pattern matches at all."""
    return HttpError(404, "not_found", f"no route for {request.method} {request.path}")


def _bad(field: str, message: str) -> HttpError:
    return HttpError(400, "invalid_request", message, details={"field": field})


def _validated_score(request: Request) -> tuple[str, Any, str, str | None]:
    """Validate a scores body; returns ``(name, stored value, data_type, comment)``.

    Raises the shared ``400 invalid_request`` envelope naming the offending field:
    ``name`` and ``value`` are required, ``value`` must be a scalar (bool, finite number
    or string), ``comment`` at most 500 characters, ``data_type`` one of the three
    stored kinds (inferred from the value type when absent: bool -> boolean, number ->
    numeric, string -> categorical), and ``source`` is fixed to ``api``.
    """
    payload = request.json()
    if not isinstance(payload, dict):
        raise _bad("body", "request body must be a JSON object")
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise _bad("name", "'name' must be a non-empty string")
    if "value" not in payload:
        raise _bad("value", "'value' is required")
    value = payload["value"]
    if isinstance(value, bool):
        stored: Any = float(value)
    elif isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise _bad("value", "'value' must be a finite number")
        stored = float(value)
    elif isinstance(value, str):
        stored = value
    else:
        raise _bad("value", "'value' must be a string, number or boolean")
    data_type = payload.get("data_type")
    if data_type is None:
        if isinstance(value, bool):
            data_type = "boolean"
        elif isinstance(value, str):
            data_type = "categorical"
        else:
            data_type = "numeric"
    elif not isinstance(data_type, str) or data_type not in _DATA_TYPES:
        raise _bad("data_type", "'data_type' must be numeric, boolean or categorical")
    comment = payload.get("comment")
    if comment is not None and (not isinstance(comment, str) or len(comment) > _MAX_COMMENT):
        raise _bad("comment", f"'comment' must be a string of at most {_MAX_COMMENT} characters")
    source = payload.get("source")
    if source is not None and source != _SOURCE:
        raise _bad("source", f"'source' must be '{_SOURCE}'")
    return name, stored, data_type, comment


def _validated_tags(request: Request) -> tuple[list[str], list[str]]:
    """Validate a tags body ``{add?, remove?}``; returns both lists unchanged.

    Every entry must be a string of at most 32 characters (obs-model 3). The 10-tag
    ceiling is checked against the merged result in the handler, where the stored tags
    are known; unknown removals are a no-op.
    """
    payload = request.json()
    if not isinstance(payload, dict):
        raise _bad("body", "request body must be a JSON object")
    add = payload.get("add", [])
    remove = payload.get("remove", [])
    if not isinstance(add, list):
        raise _bad("add", "'add' must be an array of strings")
    if not isinstance(remove, list):
        raise _bad("remove", "'remove' must be an array of strings")
    for tag in (*add, *remove):
        if not isinstance(tag, str):
            raise _bad("tags", "tags must be strings")
        if len(tag) > _MAX_TAG_LEN:
            raise _bad("tags", f"tags must be at most {_MAX_TAG_LEN} characters")
    return list(add), list(remove)
