"""Legacy surface removal contract (plan phase-8 task 9, acceptance criterion 9).

The ``/admin`` routes, the shim counters and ``LAYA_ADMIN_TOKEN`` are gone in v0.1.0:
every removed path answers the standard ``404 not_found`` envelope (through the static
catch-all, which is how a real server dispatches it), ``X-Admin-Token`` authenticates
nothing, the env variable is ignored by ``Config.load``, and ``/api/v1/meta`` carries
no ``deprecation`` counter map.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from layawatch.api.health import add_meta_routes
from layawatch.api.keys import add_key_routes
from layawatch.auth.sessions import authenticate, gate
from layawatch.config import Config
from layawatch.http.router import Router
from layawatch.http.static import mount as mount_static
from layawatch.http.types import HttpError, Request, Response
from layawatch.store.db import connect, migrate

REMOVED_GET_PATHS = (
    "/admin",
    "/admin/api/stats",
    "/admin/api/logs",
    "/admin/api/keys",
    "/admin/api/auth",
    "/admin/api/models",
    "/admin/api/bogus",
)

PROBE_BODY = b'{"name": "token probe"}'


def _build(tmp_path: Path) -> tuple[Router, Config, str]:
    """Migrated database plus the production route shape: API routes and the static mount."""
    db_path = tmp_path / "state.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    config = Config(state_dir=tmp_path, db_path=db_path, session_secret="sess-shhh")
    router = Router()
    add_key_routes(router, db_path=db_path)
    add_meta_routes(
        router,
        config=config,
        started_at=1_790_000_000.0,
        counters=lambda: {},
        sse_clients=lambda: 0,
    )
    mount_static(router, tmp_path / "web-out-absent")
    return router, config, str(db_path)


def _call(
    router: Router, method: str, target: str, *, headers: dict[str, str] | None = None
) -> Response:
    return router.dispatch(
        Request.build(method, target, headers or {}, PROBE_BODY, "feedbeef", "127.0.0.1")
    )


def _error(response: Response) -> dict:
    return json.loads(response.body)["error"]


def test_every_removed_admin_get_path_is_a_404_envelope(tmp_path) -> None:
    router, _config, _db = _build(tmp_path)
    for path in REMOVED_GET_PATHS:
        response = _call(router, "GET", path)
        assert response.status == 404, path
        assert _error(response)["code"] == "not_found", path
        assert "Deprecation" not in response.headers, path
        assert "Link" not in response.headers, path


def test_meta_has_no_deprecation_counter_map(tmp_path) -> None:
    router, _config, _db = _build(tmp_path)
    response = _call(router, "GET", "/api/v1/meta")
    assert response.status == 200
    assert "deprecation" not in json.loads(response.body)


def test_x_admin_token_authenticates_nothing(tmp_path) -> None:
    router, _config, db_path = _build(tmp_path)
    request = Request.build(
        "POST", "/api/v1/keys", {"x-admin-token": "s3cret"}, PROBE_BODY, "feedbeef", "127.0.0.1"
    )
    assert authenticate(request, db_path) is None
    conn = connect(db_path)
    try:
        with pytest.raises(HttpError) as excinfo:
            gate(request, conn, db_path, "keys.write")
    finally:
        conn.close()
    assert excinfo.value.status == 401
    assert excinfo.value.code == "missing_or_invalid_credential"
    response = _call(router, "POST", "/api/v1/keys", headers={"x-admin-token": "s3cret"})
    assert response.status == 401
    assert _error(response)["code"] == "missing_or_invalid_credential"


def test_laya_admin_token_env_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("LAYA_ADMIN_TOKEN", "s3cret")
    config = Config.load()  # reads os.environ: the variable must not surface anywhere
    assert not hasattr(config, "admin_token")
