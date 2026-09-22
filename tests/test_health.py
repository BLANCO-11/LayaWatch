"""GET /healthz payload contract for both auth states."""
from __future__ import annotations

import json

from layawatch.api.health import add_health_routes
from layawatch.http.router import Router
from layawatch.http.types import Request


class StubAdapter:
    """Duck-typed stand-in for the engine adapter: only loaded() and device()."""

    def __init__(self, models: list[str], device: str) -> None:
        self._models = models
        self._device = device

    def loaded(self) -> list[str]:
        return list(self._models)

    def device(self) -> str:
        return self._device


def get_healthz(router: Router) -> dict:
    response = router.dispatch(
        Request.build("GET", "/healthz", {}, b"", "feedbeef", "127.0.0.1")
    )
    assert response.status == 200
    return json.loads(response.body)


def test_healthz_when_auth_disabled() -> None:
    router = Router()
    add_health_routes(router, StubAdapter(["english", "multilingual"], "cpu"))
    payload = get_healthz(router)
    assert payload == {
        "status": "ok",
        "models": ["english", "multilingual"],
        "device": "cpu",
        "auth_enabled": False,
    }
    assert payload["auth_enabled"] is False


def test_healthz_when_auth_enabled() -> None:
    router = Router()
    add_health_routes(router, StubAdapter([], "auto"), auth_enabled=True)
    payload = get_healthz(router)
    assert payload == {
        "status": "ok",
        "models": [],
        "device": "auto",
        "auth_enabled": True,
    }
    assert payload["auth_enabled"] is True
