"""Router dispatch contract: precedence, method handling, error envelope, request id."""
from __future__ import annotations

import json
import re

from layawatch.http.router import Router
from layawatch.http.types import HttpError, Request, Response, text_response

_REQUEST_ID = "a1b2c3d4"


def make_request(method: str = "GET", target: str = "/hello") -> Request:
    return Request.build(method, target, {}, b"", _REQUEST_ID, "127.0.0.1")


def hello(_request: Request) -> Response:
    return text_response("hi")


def create_thing(_request: Request) -> Response:
    return text_response("created", status=201)


def boom(_request: Request) -> Response:
    raise ValueError("db password is hunter2")


def test_exact_route_returns_200() -> None:
    router = Router()
    router.add("GET", "/hello", hello)
    response = router.dispatch(make_request())
    assert response.status == 200
    assert response.body == b"hi"
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"


def test_unknown_path_returns_not_found_envelope() -> None:
    router = Router()
    router.add("GET", "/hello", hello)
    response = router.dispatch(make_request(target="/missing"))
    assert response.status == 404
    payload = json.loads(response.body)
    assert set(payload) == {"error"}
    error = payload["error"]
    assert error["code"] == "not_found"
    assert set(error) == {"code", "message"}
    assert isinstance(error["message"], str)


def test_method_mismatch_returns_405_with_allow() -> None:
    router = Router()
    router.add("POST", "/things", create_thing)
    response = router.dispatch(make_request("GET", "/things"))
    assert response.status == 405
    allowed = {name.strip() for name in response.headers["Allow"].split(",")}
    assert allowed == {"POST", "OPTIONS"}


def test_head_falls_back_to_get_headers_without_body() -> None:
    router = Router()
    router.add("GET", "/hello", hello)
    get = router.dispatch(make_request())
    head = router.dispatch(make_request("HEAD"))
    assert head.status == 200
    assert head.body == b""
    assert head.headers["Content-Length"] == "2"
    assert head.headers["Content-Length"] == get.headers["Content-Length"]
    assert head.headers["Content-Type"] == get.headers["Content-Type"]


def test_options_returns_204_with_allow() -> None:
    router = Router()
    router.add("GET", "/hello", hello)
    response = router.dispatch(make_request("OPTIONS"))
    assert response.status == 204
    assert response.body == b""
    assert "Content-Length" not in response.headers
    allowed = {name.strip() for name in response.headers["Allow"].split(",")}
    assert allowed == {"GET", "HEAD", "OPTIONS"}


def test_304_carries_no_content_length() -> None:
    def not_modified(_request: Request) -> Response:
        return Response(status=304, headers={"ETag": 'W/"abc"'})

    router = Router()
    router.add("GET", "/asset.js", not_modified)
    response = router.dispatch(make_request(target="/asset.js"))
    assert response.status == 304
    assert "Content-Length" not in response.headers
    assert response.headers["ETag"] == 'W/"abc"'
    assert response.headers["X-Request-Id"] == _REQUEST_ID


def test_http_error_renders_envelope_with_details() -> None:
    def bad(_request: Request) -> Response:
        raise HttpError(400, "invalid_filter", "status must be an integer", {"field": "status"})

    router = Router()
    router.add("GET", "/bad", bad)
    response = router.dispatch(make_request(target="/bad"))
    assert response.status == 400
    error = json.loads(response.body)["error"]
    assert error == {
        "code": "invalid_filter",
        "message": "status must be an integer",
        "details": {"field": "status"},
    }


def test_unhandled_exception_returns_generic_500() -> None:
    router = Router()
    router.add("GET", "/boom", boom)
    response = router.dispatch(make_request(target="/boom"))
    assert response.status == 500
    error = json.loads(response.body)["error"]
    assert error["code"] == "internal_error"
    assert set(error) == {"code", "message"}
    text = response.body.decode("utf-8")
    assert "hunter2" not in text
    assert "ValueError" not in text


def test_request_id_on_every_response() -> None:
    router = Router()
    router.add("GET", "/hello", hello)
    router.add("GET", "/boom", boom)
    cases = [
        ("GET", "/hello", 200),
        ("GET", "/missing", 404),
        ("POST", "/hello", 405),
        ("OPTIONS", "/hello", 204),
        ("GET", "/boom", 500),
    ]
    for method, target, expected in cases:
        response = router.dispatch(make_request(method, target))
        assert response.status == expected
        request_id = response.headers.get("X-Request-Id", "")
        assert request_id == _REQUEST_ID
        assert re.fullmatch(r"[0-9a-f]{8}", request_id)


def test_prefix_and_exact_precedence_with_wildcard() -> None:
    def tag(name: str):
        def handler(_request: Request) -> Response:
            return text_response(name)

        return handler

    router = Router()
    router.add("GET", "/*", tag("wildcard"))
    router.add("GET", "/api/*", tag("api"))
    router.add("GET", "/api/v1/*", tag("v1"))
    router.add("GET", "/api/v1/items", tag("exact"))

    # Exact beats prefix even against the longest one.
    assert router.dispatch(make_request(target="/api/v1/items")).body == b"exact"
    # Longest matching prefix wins.
    assert router.dispatch(make_request(target="/api/v1/items/9")).body == b"v1"
    assert router.dispatch(make_request(target="/api/health")).body == b"api"
    # The wildcard catches otherwise-unmatched paths instead of a 404.
    assert router.dispatch(make_request(target="/anything")).body == b"wildcard"
    # Path is known through the wildcard, so a wrong method is 405, not 404.
    response = router.dispatch(make_request("DELETE", "/anything"))
    assert response.status == 405
    allowed = {name.strip() for name in response.headers["Allow"].split(",")}
    assert allowed == {"GET", "HEAD", "OPTIONS"}
