"""GET /healthz registration: liveness payload straight from the engine adapter."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from layawatch.http.types import Request, Response, json_response

if TYPE_CHECKING:
    from collections.abc import Iterable

    from layawatch.http.router import Router


class Adapter(Protocol):
    """Duck-typed engine adapter surface used by the health payload."""

    def loaded(self) -> Iterable[str]:
        ...

    def device(self) -> str:
        ...


def add_health_routes(router: Router, adapter: Adapter, *, auth_enabled: bool = False) -> None:
    """Register ``GET /healthz`` returning status, loaded models, device, and auth state."""

    def healthz(_request: Request) -> Response:
        return json_response(
            {
                "status": "ok",
                "models": list(adapter.loaded()),
                "device": adapter.device(),
                "auth_enabled": auth_enabled,
            }
        )

    router.add("GET", "/healthz", healthz)
