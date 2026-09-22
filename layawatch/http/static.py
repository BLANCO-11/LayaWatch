"""Static serving for the Next.js export in ``web/out``.

``mount`` registers the catch-all ``GET /*`` route; call it after the API routes so exact
patterns win ties. ``resolve`` maps a request path onto a file under ``web_root`` (or the
fallback pages when the export is missing) and is exported for tests. API-shaped paths never
resolve to a file here: they raise ``HttpError(404, "not_found")`` so API clients always
receive the JSON error envelope even when the static catch-all matched.

The export pre-renders one exemplar id per dynamic route (``/traces/detail``): a deep link
to any other ``/traces/<id>`` falls back to that exported page rather than 404, and the
page reads the real id from the URL on the client.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

from layawatch.http.types import HttpError, Request, Response
from layawatch.log import get_logger

if TYPE_CHECKING:
    from layawatch.http.router import Router

_log = get_logger("static")

_API_PREFIX = "/api/"
_API_EXACT = frozenset({"/predict", "/route", "/healthz"})

# Extension allowlist; anything else is never served.
_CONTENT_TYPES: dict[str, str] = {
    "html": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "mjs": "text/javascript; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "map": "application/json; charset=utf-8",
    "svg": "image/svg+xml; charset=utf-8",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "ico": "image/x-icon",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "txt": "text/plain; charset=utf-8",
    "webmanifest": "application/manifest+json; charset=utf-8",
}

#: Dynamic routes that pre-render one exemplar id for the static export, mapped to the
#: exemplar path. A deep link to any other id (``/traces/<id>``) serves the exemplar page
#: instead of 404; the client reads the real id from the URL. Single-segment tails only.
_DEEP_LINK_FALLBACKS: tuple[tuple[str, str], ...] = (("/traces/", "/traces/detail"),)

_NEXT_STATIC_PREFIX = "/_next/static/"
_CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
_CACHE_HTML = "no-cache"
_CACHE_ONE_DAY = "public, max-age=86400"
_HTML_TYPE = "text/html; charset=utf-8"

_BUILD_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LayaWatch - console not built</title>
<style>
body { font-family: system-ui, sans-serif; line-height: 1.6; margin: 4rem auto;
       max-width: 42rem; padding: 0 1rem; color: #1c1c1c; background: #fafafa; }
code { background: #ececec; border-radius: 3px; padding: 0.05rem 0.3rem; }
pre { background: #ececec; border-radius: 4px; padding: 0.75rem 1rem; overflow-x: auto; }
a { color: #0b57d0; }
</style>
</head>
<body>
<h1>Console UI not built</h1>
<p>The LayaWatch API is running, but the static console has not been built yet.
From the <code>web/</code> directory run:</p>
<pre>npm ci && npm run build</pre>
<p>The build writes the static export into <code>web/out</code>, which this server serves at
<code>/</code>. The full console UI ships in Phase 4.</p>
<p>In the meantime the API answers: <a href="/healthz">/healthz</a>.</p>
</body>
</html>
"""

_NOT_FOUND_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>404 - Not found</title>
<style>
body { font-family: system-ui, sans-serif; line-height: 1.6; margin: 4rem auto;
       max-width: 42rem; padding: 0 1rem; color: #1c1c1c; background: #fafafa; }
a { color: #0b57d0; }
</style>
</head>
<body>
<h1>404 - Not found</h1>
<p>The requested path does not exist.
<a href="/">Back to the start</a> or check <a href="/healthz">/healthz</a>.</p>
</body>
</html>
"""


def _extension(path: str) -> str:
    """Lowercased extension of the final path segment, or ``""`` when there is none."""
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    return name[dot + 1 :].lower()


def _cache_control(request_path: str, ext: str) -> str:
    if request_path.startswith(_NEXT_STATIC_PREFIX):
        return _CACHE_IMMUTABLE
    if ext == "html":
        return _CACHE_HTML
    return _CACHE_ONE_DAY


def _candidates(path: str) -> list[str]:
    """Candidate file paths for a request path, in resolution order; ``[]`` when disallowed."""
    ext = _extension(path)
    if ext:
        return [path] if ext in _CONTENT_TYPES else []
    if path.endswith("/"):
        return [path + "index.html"]
    return [path + "/index.html", path + ".html"]


def _serve_file(candidate: str, request_path: str, root: Path) -> Response | None:
    target = (root / candidate.lstrip("/")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    try:
        body = target.read_bytes()
    except OSError as exc:
        _log.warning(f"cannot read {target}: {exc}")
        return None
    ext = _extension(candidate)
    etag = 'W/"' + hashlib.sha1(body).hexdigest() + '"'
    return Response(
        status=200,
        headers={
            "Content-Type": _CONTENT_TYPES[ext],
            "Cache-Control": _cache_control(request_path, ext),
            "ETag": etag,
        },
        body=body,
    )


def _build_page() -> Response:
    return Response(
        status=200,
        headers={"Content-Type": _HTML_TYPE, "Cache-Control": _CACHE_HTML},
        body=_BUILD_PAGE_HTML.encode("ascii"),
    )


def _not_found_page(web_root: Path) -> Response:
    page = resolve("/404.html", web_root)
    if page is not None:
        page.status = 404
        return page
    return Response(
        status=404,
        headers={"Content-Type": _HTML_TYPE, "Cache-Control": _CACHE_HTML},
        body=_NOT_FOUND_HTML.encode("ascii"),
    )


def resolve(path: str, web_root: Path) -> Response | None:
    """Resolve a request path to a 200 file response, or ``None`` on a miss.

    Percent-decodes the path once, rejects ``..`` segments and anything escaping
    ``web_root`` after ``Path.resolve()``, and never serves directories. API-shaped
    paths raise ``HttpError(404, "not_found")`` so callers render the JSON envelope.
    """
    decoded = unquote(path)
    if decoded.startswith(_API_PREFIX) or decoded in _API_EXACT:
        raise HttpError(404, "not_found", "not found")
    if "\x00" in decoded or ".." in decoded.split("/"):
        return None
    root = web_root.resolve()
    if not root.is_dir():
        if decoded in ("/", "/index.html"):
            return _build_page()
        return None
    for candidate in _candidates(decoded):
        response = _serve_file(candidate, decoded, root)
        if response is not None:
            return response
    for prefix, exemplar in _DEEP_LINK_FALLBACKS:
        tail = decoded[len(prefix) :] if decoded.startswith(prefix) else None
        if not tail or "/" in tail:
            continue
        for candidate in _candidates(exemplar):
            response = _serve_file(candidate, decoded, root)
            if response is not None:
                return response
    return None


def _make_handler(web_root: Path):
    def handle(request: Request) -> Response:
        response = resolve(request.path, web_root)
        if response is None:
            return _not_found_page(web_root)
        etag = response.headers.get("ETag")
        if etag is not None:
            wanted = next(
                (
                    value.strip()
                    for key, value in request.headers.items()
                    if key.lower() == "if-none-match"
                ),
                "",
            )
            if wanted == "*" or wanted == etag:
                return Response(
                    status=304,
                    headers={
                        "ETag": etag,
                        "Cache-Control": response.headers["Cache-Control"],
                    },
                    body=b"",
                )
        return response

    return handle


def mount(router: Router, web_root: Path) -> None:
    """Register the catch-all ``GET /*`` static route.

    Call after the API routes: exact patterns beat the prefix match and registration
    order breaks ties, so this must be the last route added.
    """
    router.add("GET", "/*", _make_handler(web_root))
