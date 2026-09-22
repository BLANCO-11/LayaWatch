"""Metrics read endpoints (docs/api-reference.md section 6).

``GET /api/v1/metrics`` charts rollup series with a server-chosen (or explicitly allowed)
step, ``/summary`` feeds the Overview KPI strip with previous-window deltas, and
``/models`` feeds the model-mix stacked bars. Handlers are thin pass-throughs onto
``store.queries``: parameter allowlisting happens here; window resolution, step choice and
response shapes are the query layer's, returned verbatim. Every handler opens a
short-lived connection per request and closes it in a finally.

Authentication and role gates arrive in Phase 3: these reads work without credentials.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layawatch.http.types import HttpError, Request, Response, json_response
from layawatch.store.db import connect
from layawatch.store.queries import (
    RANGE_SECONDS,
    InvalidFilter,
    metrics_models,
    metrics_series,
    metrics_summary,
    parse_metrics,
    parse_range,
    parse_step,
    resolve_window,
    step_for_range,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from layawatch.http.router import Router

#: Accepted query parameters per endpoint; anything else is a 400 ``invalid_filter``.
_SERIES_PARAMS = frozenset({"metrics", "range", "since", "until", "step", "model", "route"})
_SUMMARY_PARAMS = frozenset({"range"})
_MODELS_PARAMS = frozenset({"range", "since", "until"})


def add_metrics_routes(router: Router, db_path: str | Path) -> None:
    """Register the three section 6 GET routes: series, summary and model mix."""

    def series(request: Request) -> Response:
        try:
            _reject_unknown(request.query, _SERIES_PARAMS)
            names = parse_metrics(request.param("metrics"))
            since, until = _window(request.query)
            step = parse_step(request.param("step"))
            if step is None:
                step = step_for_range(until - since)
            conn = connect(db_path)
            try:
                payload = metrics_series(
                    conn,
                    names,
                    since,
                    until,
                    step,
                    request.param("model"),
                    request.param("route"),
                )
            finally:
                conn.close()
        except InvalidFilter as exc:
            raise HttpError(400, "invalid_filter", str(exc)) from None
        return json_response(payload)

    def summary(request: Request) -> Response:
        try:
            _reject_unknown(request.query, _SUMMARY_PARAMS)
            raw = request.param("range")
            range_s = RANGE_SECONDS["15m"] if raw in (None, "") else parse_range(raw)
            conn = connect(db_path)
            try:
                payload = metrics_summary(conn, range_s)
            finally:
                conn.close()
        except InvalidFilter as exc:
            raise HttpError(400, "invalid_filter", str(exc)) from None
        return json_response(payload)

    def models(request: Request) -> Response:
        try:
            _reject_unknown(request.query, _MODELS_PARAMS)
            since, until = resolve_window(request.query)
            conn = connect(db_path)
            try:
                payload = metrics_models(conn, since, until)
            finally:
                conn.close()
        except InvalidFilter as exc:
            raise HttpError(400, "invalid_filter", str(exc)) from None
        return json_response(payload)

    router.add("GET", "/api/v1/metrics", series)
    router.add("GET", "/api/v1/metrics/summary", summary)
    router.add("GET", "/api/v1/metrics/models", models)


def _reject_unknown(params: Mapping[str, Any], allowlist: frozenset[str]) -> None:
    """Reject any parameter outside ``allowlist``, naming it (docs section 2: never ignored)."""
    for key in params:
        if key not in allowlist:
            raise InvalidFilter(f"unknown query parameter {key!r}")


def _window(params: Mapping[str, Any]) -> tuple[float, float]:
    """``resolve_window`` output with the documented 15 m default width when unbounded."""
    since, until = resolve_window(params)
    if until is None:
        until = time.time()
    if since is None:
        since = until - RANGE_SECONDS["15m"]
    return since, until
