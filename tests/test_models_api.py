"""Models API contract: section 11 list shape, load/unload mapping, audit, SSE payload.

Dispatch happens in-process through a bare Router (no server, no middleware), so a 200 here
also proves the endpoints answer without credentials until Phase 3 adds role gates.
"""
from __future__ import annotations

import json

from layawatch.api.models import add_model_routes
from layawatch.config import Config
from layawatch.engine.adapter import EngineMemoryError, FakeAdapter
from layawatch.http.router import Router
from layawatch.http.types import Request, Response
from layawatch.store import db


def build(tmp_path, *, adapter=None, config=None, on_change=None):
    """Migrated database + model routes on a fresh Router; returns (router, adapter, db_path)."""
    db_path = tmp_path / "state.sqlite3"
    conn = db.connect(db_path)
    db.migrate(conn)
    conn.close()
    if adapter is None:
        adapter = FakeAdapter(models=("english", "multilingual"), initially_loaded=("english",))
    if config is None:
        config = Config(models=["english", "multilingual"])
    router = Router()
    add_model_routes(router, adapter, config, db_path, on_change=on_change)
    return router, adapter, db_path


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


def audit_rows(db_path) -> list[tuple]:
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT actor, action, target, result FROM audit_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [(row["actor"], row["action"], row["target"], row["result"]) for row in rows]


class _InFlightAdapter(FakeAdapter):
    """Lifecycle guard tripped: a second admin action while one is already running."""

    def load(self, models: list[str]) -> None:
        raise RuntimeError("a model load or unload is already in flight; retry when it finishes")


class _MemoryExhaustedAdapter(FakeAdapter):
    """MemAvailable cannot cover the checkpoints the load would build."""

    def load(self, models: list[str]) -> None:
        raise EngineMemoryError(
            "loading 1 checkpoint(s) needs 1.5 GB available memory; MemAvailable is 0.5 GB"
        )


class _GuardedAdapter:
    """Any engine construction attempt fails the test: GET must stay on loaded()/device()."""

    def loaded(self) -> list[str]:
        return ["english"]

    def device(self) -> str:
        return "cpu"

    def load(self, models: list[str]) -> None:
        raise AssertionError("GET /api/v1/models must not load")

    def predict(self, state, questions, *, model=None, task=None) -> dict:
        raise AssertionError("GET /api/v1/models must not predict")


def test_list_shape_matches_section_11(tmp_path) -> None:
    router, _adapter, _db_path = build(tmp_path)
    response = call(router, "GET", "/api/v1/models")

    assert response.status == 200
    payload = json.loads(response.body)
    assert set(payload) == {"loaded", "available", "default", "device", "rss_mb", "stats"}
    assert payload["loaded"] == ["english"]
    assert payload["available"] == ["english", "multilingual"]
    assert payload["default"] == ["english", "multilingual"]
    # D-014: every catalog entry carries the four stat columns; the fake adapter emits no
    # model.load span and a fresh database has no rollup rows, so the nulls are the contract.
    assert payload["stats"] == {
        "english": {"size_bytes": None, "load_ms": None, "requests_24h": 0, "p50_ms": None},
        "multilingual": {"size_bytes": None, "load_ms": None, "requests_24h": 0, "p50_ms": None},
    }
    assert payload["device"] == "fake"
    assert isinstance(payload["rss_mb"], (int, float))
    assert payload["rss_mb"] >= 0


def test_available_honors_english_only(tmp_path) -> None:
    config = Config(models=["english", "multilingual"], english_only=True)
    router, _adapter, _db_path = build(tmp_path, config=config)
    payload = json.loads(call(router, "GET", "/api/v1/models").body)

    assert payload["available"] == ["english"]
    assert payload["default"] == ["english"]


def test_list_never_constructs_the_engine(tmp_path) -> None:
    router, _adapter, _db_path = build(tmp_path, adapter=_GuardedAdapter())
    response = call(router, "GET", "/api/v1/models")

    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["loaded"] == ["english"]
    assert payload["device"] == "cpu"


def test_load_happy_path_audits_and_publishes(tmp_path) -> None:
    events: list[dict] = []
    adapter = FakeAdapter(models=("english", "multilingual"))
    router, adapter, db_path = build(tmp_path, adapter=adapter, on_change=events.append)
    response = call(router, "POST", "/api/v1/models/load", {"models": ["english", "multilingual"]})

    assert response.status == 200
    assert json.loads(response.body) == {"loaded": ["english", "multilingual"]}
    assert adapter.loaded() == ["english", "multilingual"]
    assert audit_rows(db_path) == [
        ("api", "model.load", "english,multilingual", "ok")
    ]
    assert events == [{"loaded": ["english", "multilingual"], "action": "load"}]


def test_unknown_model_is_400_invalid_request_without_audit(tmp_path) -> None:
    router, _adapter, db_path = build(
        tmp_path, adapter=FakeAdapter(models=("english",), initially_loaded=("english",))
    )
    response = call(router, "POST", "/api/v1/models/load", {"models": ["multilingual"]})

    assert response.status == 400
    error = error_of(response)
    assert error["code"] == "invalid_request"
    assert error["message"] == "unknown model(s): multilingual"
    assert audit_rows(db_path) == []


def test_in_flight_runtime_error_is_409_load_in_progress(tmp_path) -> None:
    adapter = _InFlightAdapter(
        models=("english", "multilingual"), initially_loaded=("english",)
    )
    router, _adapter, _db_path = build(tmp_path, adapter=adapter)
    response = call(router, "POST", "/api/v1/models/load", {"models": ["multilingual"]})

    assert response.status == 409
    error = error_of(response)
    assert error["code"] == "load_in_progress"
    assert "already in flight" in error["message"]


def test_engine_memory_error_is_503_with_message_in_details(tmp_path) -> None:
    adapter = _MemoryExhaustedAdapter(
        models=("english", "multilingual"), initially_loaded=("english",)
    )
    router, _adapter, _db_path = build(tmp_path, adapter=adapter)
    response = call(router, "POST", "/api/v1/models/load", {"models": ["multilingual"]})

    assert response.status == 503
    error = error_of(response)
    assert error["code"] == "insufficient_memory"
    assert "MemAvailable" in error["message"]
    assert error["details"]["message"] == error["message"]


def test_unload_is_idempotent_and_publishes(tmp_path) -> None:
    events: list[dict] = []
    adapter = FakeAdapter(
        models=("english", "multilingual"), initially_loaded=("english", "multilingual")
    )
    router, adapter, db_path = build(tmp_path, adapter=adapter, on_change=events.append)

    first = call(router, "POST", "/api/v1/models/unload", {"models": ["multilingual"]})
    second = call(router, "POST", "/api/v1/models/unload", {"models": ["multilingual"]})

    assert first.status == 200
    assert json.loads(first.body) == {"loaded": ["english"]}
    assert second.status == 200  # unloading a checkpoint that is already gone stays 200
    assert json.loads(second.body) == {"loaded": ["english"]}
    assert adapter.loaded() == ["english"]
    assert [event["action"] for event in events] == ["unload", "unload"]
    assert audit_rows(db_path) == [
        ("api", "model.unload", "multilingual", "ok"),
        ("api", "model.unload", "multilingual", "ok"),
    ]


def test_bad_action_and_models_are_400_invalid_request(tmp_path) -> None:
    router, _adapter, _db_path = build(tmp_path)

    bad_action = call(
        router, "POST", "/api/v1/models", {"action": "explode", "models": ["english"]}
    )
    assert bad_action.status == 400
    assert error_of(bad_action)["details"] == {"field": "action"}

    empty_models = call(router, "POST", "/api/v1/models/load", {"models": []})
    assert empty_models.status == 400
    assert error_of(empty_models)["details"] == {"field": "models"}

    scalar_models = call(router, "POST", "/api/v1/models/unload", {"models": "english"})
    assert scalar_models.status == 400
    assert error_of(scalar_models)["details"] == {"field": "models"}

    non_object = call(router, "POST", "/api/v1/models/load", b"[1, 2]")
    assert non_object.status == 400
    assert error_of(non_object)["details"] == {"field": "body"}


def test_model_admin_action_body_loads_and_unloads(tmp_path) -> None:
    """POST /api/v1/models accepts the legacy {action, models} body the section 14 shim maps."""
    events: list[dict] = []
    adapter = FakeAdapter(models=("english", "multilingual"))
    router, adapter, _db_path = build(tmp_path, adapter=adapter, on_change=events.append)

    loaded = call(router, "POST", "/api/v1/models", {"action": "load", "models": ["english"]})
    unloaded = call(router, "POST", "/api/v1/models", {"action": "unload", "models": ["english"]})

    assert loaded.status == 200
    assert json.loads(loaded.body) == {"loaded": ["english"]}
    assert unloaded.status == 200
    assert json.loads(unloaded.body) == {"loaded": []}
    assert adapter.loaded() == []
    assert [event["action"] for event in events] == ["load", "unload"]
