"""Legacy shim contract: redirect, forwarding, deprecation headers, counters (api-reference 14).

One Router carries the sibling metrics/logs routes, this wave's model routes and the shims,
so every comparison dispatches both the legacy path and its mapped target against identical
state. Window math in ``/api/v1/metrics/summary`` reads ``time.time()`` per call, so the
clock is frozen across both dispatches to make the byte comparison deterministic; the model
list pins ``rss_mb`` for the same reason.
"""
from __future__ import annotations

import json
import time

from layawatch.api.logs import add_logs_routes
from layawatch.api.metrics import add_metrics_routes

from layawatch.api import legacy, models
from layawatch.api.models import add_model_routes
from layawatch.config import Config
from layawatch.engine.adapter import FakeAdapter
from layawatch.http.router import Router
from layawatch.http.types import Request, Response
from layawatch.store import db

STATS_LINK = '</api/v1/metrics/summary>; rel="successor-version"'
LOGS_LINK = '</api/v1/logs>; rel="successor-version"'
MODELS_LINK = '</api/v1/models>; rel="successor-version"'
KEYS_LINK = '</api/v1/keys>; rel="successor-version"'


def build(tmp_path, monkeypatch):
    """Migrated database and one Router with metrics, logs, models and legacy routes."""
    db_path = tmp_path / "state.sqlite3"
    conn = db.connect(db_path)
    db.migrate(conn)
    conn.close()
    router = Router()
    add_metrics_routes(router, db_path)
    add_logs_routes(router, db_path)
    add_model_routes(
        router,
        FakeAdapter(models=("english", "multilingual"), initially_loaded=("english",)),
        Config(models=["english", "multilingual"]),
        db_path,
    )
    legacy.add_legacy_routes(router)
    legacy._counts.clear()  # module-global counters: isolate every test
    monkeypatch.setattr(time, "time", lambda: 1_790_000_000.0)  # freeze window math
    monkeypatch.setattr(models, "_rss_mb", lambda: 42.0)
    return router


def call(
    router: Router,
    method: str,
    target: str,
    body: dict | bytes | None = None,
    request_id: str = "a1b2c3d4",
) -> Response:
    raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body))
    return router.dispatch(Request.build(method, target, {}, raw, request_id, "127.0.0.1"))


def error_of(response: Response) -> dict:
    return json.loads(response.body)["error"]


def test_admin_redirects_to_root_without_deprecation_header(tmp_path, monkeypatch) -> None:
    router = build(tmp_path, monkeypatch)
    response = call(router, "GET", "/admin")

    assert response.status == 302
    assert response.headers["Location"] == "/"
    assert response.body == b""
    assert "Deprecation" not in response.headers
    assert legacy.deprecation_counts()["/admin"] == 1


def test_stats_shim_body_equals_summary_with_exact_headers(tmp_path, monkeypatch) -> None:
    router = build(tmp_path, monkeypatch)
    direct = call(router, "GET", "/api/v1/metrics/summary")
    shim = call(router, "GET", "/admin/api/stats")

    assert direct.status == 200
    assert shim.status == 200
    assert shim.body == direct.body
    assert shim.headers["Deprecation"] == "true"
    assert shim.headers["Link"] == STATS_LINK


def test_logs_shim_body_equals_logs_with_exact_headers(tmp_path, monkeypatch) -> None:
    router = build(tmp_path, monkeypatch)
    direct = call(router, "GET", "/api/v1/logs")
    shim = call(router, "GET", "/admin/api/logs")

    assert direct.status == 200
    assert shim.status == 200
    assert shim.body == direct.body
    assert shim.headers["Deprecation"] == "true"
    assert shim.headers["Link"] == LOGS_LINK


def test_models_shim_body_equals_models_with_exact_headers(tmp_path, monkeypatch) -> None:
    router = build(tmp_path, monkeypatch)
    direct = call(router, "GET", "/api/v1/models")
    shim = call(router, "GET", "/admin/api/models")

    assert direct.status == 200
    assert shim.status == 200
    assert shim.body == direct.body
    assert shim.headers["Deprecation"] == "true"
    assert shim.headers["Link"] == MODELS_LINK


def test_models_post_shim_forwards_the_legacy_action_body(tmp_path, monkeypatch) -> None:
    """The old admin page POSTs {action, models}; the shim maps it onto POST /api/v1/models."""
    router = build(tmp_path, monkeypatch)
    body = {"action": "load", "models": ["multilingual"]}
    direct = call(router, "POST", "/api/v1/models", body)
    shim = call(router, "POST", "/admin/api/models", body)

    assert direct.status == 200
    assert shim.status == 200
    assert shim.body == direct.body
    assert shim.headers["Deprecation"] == "true"
    assert shim.headers["Link"] == MODELS_LINK
    assert legacy.deprecation_counts()["/admin/api/models"] == 1  # direct call is not a shim


def test_unknown_legacy_path_is_404_envelope_without_deprecation(tmp_path, monkeypatch) -> None:
    router = build(tmp_path, monkeypatch)
    response = call(router, "GET", "/admin/api/bogus")

    assert response.status == 404
    assert error_of(response)["code"] == "not_found"
    assert "Deprecation" not in response.headers
    assert "Link" not in response.headers
    assert legacy.deprecation_counts() == {}  # never-routed paths are not shim calls


def test_stats_counter_increments_per_shim_hit(tmp_path, monkeypatch) -> None:
    router = build(tmp_path, monkeypatch)
    call(router, "GET", "/admin/api/stats")
    call(router, "GET", "/admin/api/stats")

    assert legacy.deprecation_counts()["/admin/api/stats"] == 2


def test_keys_shim_counts_even_while_its_target_is_phase3_404(tmp_path, monkeypatch) -> None:
    """GET/DELETE /admin/api/keys forward to /api/v1/keys, which lands in Phase 3: the shim
    still answers with the target's JSON 404 envelope plus its own deprecation headers."""
    router = build(tmp_path, monkeypatch)
    get = call(router, "GET", "/admin/api/keys")
    delete = call(router, "DELETE", "/admin/api/keys")

    for response in (get, delete):
        assert response.status == 404
        assert error_of(response)["code"] == "not_found"
        assert response.headers["Deprecation"] == "true"
        assert response.headers["Link"] == KEYS_LINK
    assert legacy.deprecation_counts()["/admin/api/keys"] == 2  # one counter per legacy path


def test_forwarding_preserves_x_request_id(tmp_path, monkeypatch) -> None:
    router = build(tmp_path, monkeypatch)
    response = call(router, "GET", "/admin/api/stats", request_id="a1b2c3d4")

    assert response.headers["X-Request-Id"] == "a1b2c3d4"
    assert response.headers["Deprecation"] == "true"


def test_forwarding_preserves_the_query_string(tmp_path, monkeypatch) -> None:
    router = build(tmp_path, monkeypatch)
    direct = call(router, "GET", "/api/v1/metrics/summary?range=15m")
    shim = call(router, "GET", "/admin/api/stats?range=15m")

    assert direct.status == 200
    assert shim.status == 200
    assert shim.body == direct.body
    assert shim.headers["Link"] == STATS_LINK
