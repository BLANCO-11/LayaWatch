"""Legacy ``/admin`` compatibility shims (docs/api-reference.md section 14).

``GET /admin`` answers ``302 Location: /`` so old bookmarks land on the new console; every
``/admin/api/*`` endpoint forwards to its mapped ``/api/v1`` target by rebuilding the Request
with the mapped path and dispatching on the same router, then marks the response
``Deprecation: true`` with a ``Link: <target>; rel="successor-version"`` successor header
(``X-Request-Id`` from the inner dispatch survives untouched). Forwarding keeps method, body,
headers and query string intact, so legacy clients - including the old admin page sending
``{action, models}`` to ``POST /admin/api/models`` - behave exactly as they did.

Each shim call increments a thread-safe per-endpoint counter keyed by the legacy path before
the forward runs, so the count reflects real traffic even when the target answers 404 (the
``/admin/api/keys`` and ``/admin/api/auth`` targets arrive in Phase 3).
``deprecation_counts()`` hands the snapshot to ``/api/v1/meta`` for the Settings card.

Authentication and role gates arrive in Phase 3: the shims forward whatever credentials the
request carries and enforce nothing themselves. Registered shims are exact paths, so they win
over any mounted ``/*`` catch-all.

These shims are deleted at Phase 8.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from layawatch.http.types import Request, Response, empty_response

if TYPE_CHECKING:
    from layawatch.http.router import Router

#: ``(method, legacy path, mapped target)`` per the section 14 table. All methods sharing a
#: legacy path share one counter and one target, so GET/POST/DELETE /admin/api/keys map alike.
_FORWARDS: tuple[tuple[str, str, str], ...] = (
    ("GET", "/admin/api/stats", "/api/v1/metrics/summary"),
    ("GET", "/admin/api/logs", "/api/v1/logs"),
    ("GET", "/admin/api/keys", "/api/v1/keys"),
    ("POST", "/admin/api/keys", "/api/v1/keys"),
    ("DELETE", "/admin/api/keys", "/api/v1/keys"),
    ("GET", "/admin/api/auth", "/api/v1/keys/auth"),
    ("POST", "/admin/api/auth", "/api/v1/keys/auth"),
    ("GET", "/admin/api/models", "/api/v1/models"),
    ("POST", "/admin/api/models", "/api/v1/models"),
)

#: Per-endpoint shim call counts, guarded by ``_LOCK``; ``/admin`` counts as a shim call too
#: (section 14: every shim call increments a counter) even though it only redirects.
_counts: dict[str, int] = {}
_lock = threading.Lock()


def deprecation_counts() -> dict[str, int]:
    """Snapshot copy of the per-endpoint legacy shim counters for ``/api/v1/meta``."""
    with _lock:
        return dict(_counts)


def add_legacy_routes(router: Router) -> None:
    """Register ``GET /admin`` and every section 14 ``/admin/api/*`` forward on ``router``."""

    def admin_redirect(request: Request) -> Response:
        _bump(request.path)
        # The redirect itself is not an API shim response: no Deprecation header here.
        return empty_response(302, {"Location": "/"})

    router.add("GET", "/admin", admin_redirect)
    for _method, legacy_path, target in _FORWARDS:
        router.add(_method, legacy_path, _forward(router, legacy_path, target))


def _forward(router: Router, legacy_path: str, target: str):
    """Build the handler that counts ``legacy_path`` then re-dispatches onto ``target``."""

    def forward(request: Request) -> Response:
        _bump(legacy_path)
        query = urlencode(request.query, doseq=True)
        rebuilt = Request.build(
            request.method,
            f"{target}?{query}" if query else target,
            dict(request.headers),
            request.body,
            request.request_id,
            request.client_ip,
        )
        response = router.dispatch(rebuilt)
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = f'<{target}>; rel="successor-version"'
        return response

    return forward


def _bump(key: str) -> None:
    with _lock:
        _counts[key] = _counts.get(key, 0) + 1
