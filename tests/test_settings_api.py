"""Settings read view: safe config snapshot, raw settings rows, secret guard, note."""
from __future__ import annotations

import json
from pathlib import Path

from layawatch.api.health import _SAFE_CONFIG, safe_config_snapshot
from layawatch.api.settings import add_settings_routes
from layawatch.config import Config
from layawatch.http.router import Router
from layawatch.http.types import Request, Response
from layawatch.store import db

NOTE = "runtime settings override config where present; writes gated by settings.write (admin+)"


def _build(tmp_path: Path) -> tuple[Router, Config]:
    db_path = tmp_path / "state.sqlite3"
    conn = db.connect(db_path)
    db.migrate(conn)
    for key, value in (
        ("retention_traces", "25000"),
        ("stream_tick", "5"),
        ("session_secret", "must-never-leave"),
        ("api_token", "lay_should_not_leak"),
        ("db_password", "hunter2"),
    ):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)", (key, value, 1.0)
        )
    conn.commit()
    conn.close()
    config = Config(admin_token="lay_admin", session_secret="sess_shhh", bootstrap_owner="o:p")
    router = Router()
    add_settings_routes(router, config, db_path)
    return router, config


def call(router: Router, target: str) -> Response:
    return router.dispatch(Request.build("GET", target, {}, b"", "feedbeef", "127.0.0.1"))


def get_payload(router: Router) -> dict:
    response = call(router, "/api/v1/settings")
    assert response.status == 200
    return json.loads(response.body)


def test_config_is_exactly_the_safe_allowlist_and_leaks_no_credential(tmp_path: Path) -> None:
    router, config = _build(tmp_path)
    payload = get_payload(router)
    assert set(payload["config"]) == set(_SAFE_CONFIG)
    assert payload["config"] == safe_config_snapshot(config)
    for secret in ("admin_token", "session_secret", "bootstrap_owner"):
        assert secret not in payload["config"]
    for path in ("state_dir", "db_path", "web_root"):
        assert path not in payload["config"]
    assert payload["config"]["port"] == 8050  # values come from the live config


def test_settings_rows_round_trip_as_raw_text(tmp_path: Path) -> None:
    router, _config = _build(tmp_path)
    payload = get_payload(router)
    assert payload["settings"] == {"retention_traces": "25000", "stream_tick": "5"}


def test_secret_like_settings_rows_are_omitted(tmp_path: Path) -> None:
    router, _config = _build(tmp_path)
    settings = get_payload(router)["settings"]
    for key in ("session_secret", "api_token", "db_password"):
        assert key not in settings


def test_note_states_precedence_and_the_phase_3_write_boundary(tmp_path: Path) -> None:
    router, _config = _build(tmp_path)
    assert get_payload(router)["note"] == NOTE


def test_unknown_parameter_is_400_invalid_filter(tmp_path: Path) -> None:
    router, _config = _build(tmp_path)
    response = call(router, "/api/v1/settings?verbose=1")
    assert response.status == 400
    error = json.loads(response.body)["error"]
    assert error["code"] == "invalid_filter"
    assert "verbose" in error["message"]
