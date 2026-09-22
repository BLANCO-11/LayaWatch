"""Behavioral tests for static file serving: resolution, cache headers, safety, fallbacks."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from layawatch.http.static import mount, resolve
from layawatch.http.types import HttpError, Request, Response

Handler = Callable[[Request], Response]


class RecordingRouter:
    """Duck-typed stand-in for the router: records registrations in order."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        self.routes.append((method, pattern, handler))


def _mount(root: Path) -> Handler:
    router = RecordingRouter()
    mount(router, root)
    _method, _pattern, handler = router.routes[-1]
    return handler


def _get(handler: Handler, target: str, headers: dict[str, str] | None = None) -> Response:
    request = Request.build("GET", target, headers or {}, b"", "0badc0de", "127.0.0.1")
    return handler(request)


def _passthrough(request: Request) -> Response:
    return Response()


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "out"
    (root / "_next" / "static" / "chunks").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
    (root / "about.html").write_text("<h1>about</h1>", encoding="utf-8")
    (root / "docs" / "index.html").write_text("<h1>docs index</h1>", encoding="utf-8")
    (root / "_next" / "static" / "chunks" / "app.js").write_text("console.log(1)", "utf-8")
    (root / "style.css").write_text("body{}", encoding="utf-8")
    # Sits next to the export, outside web_root: must never be served.
    (tmp_path / "secret.txt").write_text("TOP-SECRET-VALUE", encoding="utf-8")
    return root


def test_mount_registers_catch_all_last(tmp_path: Path) -> None:
    router = RecordingRouter()
    router.add("GET", "/healthz", _passthrough)
    router.add("POST", "/predict", _passthrough)
    mount(router, tmp_path / "out")
    assert [(method, pattern) for method, pattern, _ in router.routes] == [
        ("GET", "/healthz"),
        ("POST", "/predict"),
        ("GET", "/*"),
    ]


def test_serves_index_html(site: Path) -> None:
    response = _get(_mount(site), "/?lang=en")
    assert response.status == 200
    assert response.body == b"<h1>home</h1>"
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.headers["Cache-Control"] == "no-cache"
    assert re.fullmatch(r'W/"[0-9a-f]{40}"', response.headers["ETag"])


def test_extensionless_serves_dot_html(site: Path) -> None:
    response = _get(_mount(site), "/about")
    assert response.status == 200
    assert response.body == b"<h1>about</h1>"
    assert response.headers["Cache-Control"] == "no-cache"


def test_trailing_slash_serves_directory_index(site: Path) -> None:
    response = _get(_mount(site), "/docs/")
    assert response.status == 200
    assert response.body == b"<h1>docs index</h1>"


def test_extensionless_serves_directory_index(site: Path) -> None:
    response = _get(_mount(site), "/docs")
    assert response.status == 200
    assert response.body == b"<h1>docs index</h1>"


def test_etag_then_if_none_match_returns_304(site: Path) -> None:
    handler = _mount(site)
    first = _get(handler, "/")
    etag = first.headers["ETag"]

    second = _get(handler, "/", {"if-none-match": etag})
    assert second.status == 304
    assert second.body == b""
    assert second.headers["ETag"] == etag
    assert second.headers["Cache-Control"] == "no-cache"

    star = _get(handler, "/", {"if-none-match": "*"})
    assert star.status == 304
    assert star.body == b""

    stale = _get(handler, "/", {"if-none-match": 'W/"0000000000000000000000000000000000000000"'})
    assert stale.status == 200
    assert stale.body == b"<h1>home</h1>"


def test_cache_control_by_path(site: Path) -> None:
    handler = _mount(site)
    asset = _get(handler, "/_next/static/chunks/app.js")
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"

    html_file = _get(handler, "/about.html")
    assert html_file.headers["Cache-Control"] == "no-cache"

    route_page = _get(handler, "/about")
    assert route_page.headers["Cache-Control"] == "no-cache"

    plain_asset = _get(handler, "/style.css")
    assert plain_asset.headers["Cache-Control"] == "public, max-age=86400"


def test_content_types_per_extension(site: Path) -> None:
    files = {
        "data.json": '{"ok": true}',
        "pixel.png": "not-really-png",
        "font.woff2": "not-really-woff",
        "icon.svg": "<svg></svg>",
        "note.txt": "plain words",
        "app.mjs": "export {}",
        "manifest.webmanifest": "{}",
    }
    for name, content in files.items():
        (site / name).write_text(content, encoding="utf-8")
    handler = _mount(site)
    expected = {
        "/data.json": "application/json; charset=utf-8",
        "/pixel.png": "image/png",
        "/font.woff2": "font/woff2",
        "/icon.svg": "image/svg+xml; charset=utf-8",
        "/note.txt": "text/plain; charset=utf-8",
        "/app.mjs": "text/javascript; charset=utf-8",
        "/manifest.webmanifest": "application/manifest+json; charset=utf-8",
    }
    for target, content_type in expected.items():
        response = _get(handler, target)
        assert response.status == 200, target
        assert response.headers["Content-Type"] == content_type, target


def test_raw_traversal_rejected(site: Path) -> None:
    handler = _mount(site)
    assert resolve("/../secret.txt", site) is None
    response = _get(handler, "/../secret.txt")
    assert response.status == 404
    assert b"TOP-SECRET-VALUE" not in response.body


def test_encoded_traversal_rejected(site: Path) -> None:
    handler = _mount(site)
    assert resolve("/%2e%2e/secret.txt", site) is None
    response = _get(handler, "/%2e%2e/secret.txt")
    assert response.status == 404
    assert b"TOP-SECRET-VALUE" not in response.body


def test_symlink_escape_rejected(site: Path, tmp_path: Path) -> None:
    (site / "leak.txt").symlink_to(tmp_path / "secret.txt")
    handler = _mount(site)
    response = _get(handler, "/leak.txt")
    assert response.status == 404
    assert b"TOP-SECRET-VALUE" not in response.body


def test_disallowed_extension_never_served(site: Path) -> None:
    (site / "dump.sql").write_text("SELECT secret;", encoding="utf-8")
    (site / "tool.py").write_text("print('hi')", encoding="utf-8")
    handler = _mount(site)
    for target in ("/dump.sql", "/tool.py"):
        assert resolve(target, site) is None
        response = _get(handler, target)
        assert response.status == 404, target
        assert b"SELECT" not in response.body
        assert b"print" not in response.body


def test_missing_root_serves_build_instructions(tmp_path: Path) -> None:
    handler = _mount(tmp_path / "out")
    response = _get(handler, "/")
    assert response.status == 200
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    text = response.body.decode("ascii")
    assert "npm ci && npm run build" in text
    assert "web/out" in text
    assert "Phase 4" in text
    assert 'href="/healthz"' in text
    assert "<script" not in text

    from_index = _get(handler, "/index.html")
    assert from_index.status == 200
    assert from_index.body == response.body


def test_missing_deep_path_gets_404_html(site: Path) -> None:
    handler = _mount(site)
    response = _get(handler, "/no/such/page")
    assert response.status == 404
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.headers["Cache-Control"] == "no-cache"
    assert b"404" in response.body
    assert b"<script" not in response.body


def test_missing_root_deep_path_gets_404_html(tmp_path: Path) -> None:
    handler = _mount(tmp_path / "absent")
    response = _get(handler, "/deep/page")
    assert response.status == 404
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.headers["Cache-Control"] == "no-cache"


def test_api_paths_raise_not_found_envelope_error(site: Path) -> None:
    for target in ("/api/v1/nope", "/predict", "/route", "/healthz"):
        with pytest.raises(HttpError) as excinfo:
            resolve(target, site)
        assert excinfo.value.status == 404, target
        assert excinfo.value.code == "not_found", target

    handler = _mount(site)
    with pytest.raises(HttpError) as excinfo:
        _get(handler, "/api/v1/nope")
    assert excinfo.value.code == "not_found"

    # Near misses are ordinary static misses: HTML 404, not a raised API error.
    response = _get(handler, "/predictor")
    assert response.status == 404
    assert response.headers["Content-Type"].startswith("text/html")
