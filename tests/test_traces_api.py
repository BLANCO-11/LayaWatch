"""Trace API contract: list filters over HTTP, detail math, observations, scores,
tags, delete, and the Phase 3 auth boundary (every endpoint accepts a credential-less
request today). Every assertion dispatches through the real Router against a tmp db.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from seed import seed_traces

from layawatch.api.traces import add_trace_routes
from layawatch.http.router import Router
from layawatch.http.types import Request, Response
from layawatch.store.db import connect, migrate

#: Fixed epoch for fixtures without a wall-clock window (mirrors test_queries_read).
NOW = 1_790_000_000.0


def _build(tmp_path: Path, fixture):
    """Migrate a fresh database, run ``fixture(conn)``, and register the trace routes."""
    db_path = tmp_path / "state.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    try:
        fixture(conn)
        conn.commit()
    finally:
        conn.close()
    router = Router()
    add_trace_routes(router, db_path)
    return router, db_path


def _call(
    router: Router, method: str, target: str, body: Any = None, request_id: str = "a1b2c3d4"
) -> Response:
    """Dispatch one request with no headers: Phase 3 owns credentials, not this surface."""
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    return router.dispatch(Request.build(method, target, {}, raw, request_id, "127.0.0.1"))


def _body(response: Response) -> Any:
    return json.loads(response.body) if response.body else None


def _error(response: Response) -> dict:
    return _body(response)["error"]


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list:
    conn = connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _insert_trace(
    conn: Any,
    trace_id: str,
    ts: float,
    *,
    duration: float = 100.0,
    route: str = "/predict",
    status: int = 200,
    model: str | None = "english",
    forward_ms: float | None = None,
    queue_ms: float = 0.0,
    error_message: str | None = None,
    tags: str | None = None,
    meta: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO traces (id, ts_start, duration_ms, route, method, status, model,"
        " queue_ms, forward_ms, error_message, tags, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            trace_id,
            ts,
            duration,
            route,
            "POST",
            status,
            model,
            queue_ms,
            forward_ms,
            error_message,
            tags,
            meta,
        ),
    )


def _insert_obs(
    conn: Any,
    obs_id: str,
    trace_id: str,
    start_ms: float,
    *,
    type_: str = "span",
    name: str = "http.receive",
    meta: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO observations (id, trace_id, parent_id, name, type, start_ms,"
        " duration_ms, status, model, input, output, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (obs_id, trace_id, None, name, type_, start_ms, 1.0, "ok", None, None, None, meta),
    )


def _insert_score(conn: Any, score_id: str, trace_id: str, name: str, value: float,
                  ts: float) -> None:
    conn.execute(
        "INSERT INTO scores (id, trace_id, name, value, data_type, source, comment, ts)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (score_id, trace_id, name, value, "numeric", "human", None, ts),
    )


def _insert_log(conn: Any, ts: float, *, trace_id: str | None = None,
                message: str = "line") -> None:
    conn.execute(
        "INSERT INTO log_entry (ts, level, source, trace_id, message) VALUES (?,?,?,?,?)",
        (ts, "info", "server", trace_id, message),
    )


def _list_fixture(conn: Any, fresh: float) -> None:
    _insert_trace(conn, "aa000001", fresh - 1, duration=900.0, status=500, tags='["seed"]')
    _insert_trace(
        conn,
        "aa000002",
        fresh - 2,
        duration=100.0,
        status=422,
        route="/route",
        model="multilingual",
        tags='["seeded"]',
        error_message="bad request",
    )
    _insert_trace(conn, "aa000003", fresh - 100, duration=50.0, status=200)
    _insert_trace(
        conn,
        "aa000004",
        fresh - 10000,
        duration=5000.0,
        status=500,
        route="/route",
        model=None,
        tags='["prod"]',
        error_message="slow 500",
    )
    _insert_trace(
        conn,
        "deadbeef",
        fresh - 3,
        duration=700.0,
        status=503,
        error_message="upstream 100% failure",
    )
    # twelve recent 500s: enough for the section acceptance criterion (limit=10 + cursor)
    for index in range(12):
        _insert_trace(
            conn, f"{0xE0000000 + index:08x}", fresh - 20 - index, duration=5.0 + index,
            status=500,
        )


def _detail_fixture(conn: Any) -> None:
    _insert_trace(
        conn,
        "9f2c1a4b",
        NOW - 5,
        duration=412.5,
        forward_ms=391.4,
        queue_ms=5.2,
        status=500,
        tags='["seed"]',
        meta='{"sampled": true}',
    )
    # inserted out of order on purpose: the waterfall order is start_ms, then id
    _insert_obs(conn, "o3", "9f2c1a4b", 50.0)
    _insert_obs(conn, "o1", "9f2c1a4b", 10.0)
    _insert_obs(conn, "o2", "9f2c1a4b", 30.0, meta='{"depth": 2}')
    _insert_score(conn, "s1", "9f2c1a4b", "helpful", 1.0, NOW - 4)
    _insert_log(conn, NOW - 3, trace_id="9f2c1a4b", message="first")
    _insert_log(conn, NOW - 2, trace_id="9f2c1a4b", message="second")
    _insert_log(conn, NOW - 1, trace_id="other000", message="unrelated")


def _observations_fixture(conn: Any) -> None:
    _insert_trace(conn, "0b5e7a11", NOW)
    # scrambled insertion order; the event row must not reach the waterfall endpoint
    _insert_obs(conn, "ev", "0b5e7a11", 15.0, type_="event", name="error")
    _insert_obs(conn, "sp1", "0b5e7a11", 3.0)
    _insert_obs(conn, "gen", "0b5e7a11", 8.0, type_="generation")
    _insert_obs(conn, "sp2", "0b5e7a11", 12.0)


def _tags_fixture(conn: Any) -> None:
    _insert_trace(conn, "0dd00007", NOW, tags='["seed"]')


def _delete_fixture(conn: Any) -> None:
    _insert_trace(conn, "dead00a1", NOW, tags='["seed"]')
    _insert_obs(conn, "ob1", "dead00a1", 1.0)
    _insert_obs(conn, "ob2", "dead00a1", 2.0)
    _insert_score(conn, "sc1", "dead00a1", "helpful", 1.0, NOW)
    _insert_log(conn, NOW, trace_id="dead00a1", message="kept line")
    _insert_log(conn, NOW + 1, trace_id="kept0000", message="other line")


# ---------------------------------------------------------------------------
# GET /api/v1/traces: envelope, filter matrix, pagination
# ---------------------------------------------------------------------------


def test_list_envelope_shape_default_limit_and_request_id(tmp_path: Path) -> None:
    router, _db_path = _build(tmp_path, lambda conn: seed_traces(conn, n=120, span_days=1.0))
    response = _call(router, "GET", "/api/v1/traces")

    assert response.status == 200
    assert response.headers["X-Request-Id"] == "a1b2c3d4"
    page = _body(response)
    assert set(page) == {"items", "next_cursor", "total_estimate"}
    assert len(page["items"]) == 50  # default limit (api-reference 2)
    assert isinstance(page["next_cursor"], str)
    assert page["total_estimate"] == 120
    assert set(page["items"][0]) == {
        "id",
        "ts_start",
        "duration_ms",
        "route",
        "method",
        "status",
        "model",
        "route_reason",
        "queue_ms",
        "forward_ms",
        "client_key_id",
        "error_code",
        "span_count",
    }


def test_list_status_integer_and_class(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    only500 = _body(_call(router, "GET", "/api/v1/traces?status=500"))
    assert len(only500["items"]) == 14  # aa000001 + aa000004 + 12 seeded (deadbeef is 503)
    assert {item["status"] for item in only500["items"]} == {500}
    stamps = [item["ts_start"] for item in only500["items"]]
    assert stamps == sorted(stamps, reverse=True)  # newest first

    class4 = _body(_call(router, "GET", "/api/v1/traces?status=4xx"))
    assert [item["id"] for item in class4["items"]] == ["aa000002"]

    class5 = _body(_call(router, "GET", "/api/v1/traces?status=5xx"))
    assert len(class5["items"]) == 15
    assert {item["status"] for item in class5["items"]} == {500, 503}

    bad = _call(router, "GET", "/api/v1/traces?status=banana")
    assert bad.status == 400
    assert _error(bad)["code"] == "invalid_filter"
    assert "status" in _error(bad)["message"]


def test_list_route_and_model_filters(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    by_route = _body(_call(router, "GET", "/api/v1/traces?route=/route"))
    assert [item["id"] for item in by_route["items"]] == ["aa000002", "aa000004"]

    english = _body(_call(router, "GET", "/api/v1/traces?model=english"))
    assert {item["id"] for item in english["items"]} == (
        {"aa000001", "aa000003", "deadbeef"}
        | {f"{0xE0000000 + index:08x}" for index in range(12)}
    )
    multilingual = _body(_call(router, "GET", "/api/v1/traces?model=multilingual"))
    assert [item["id"] for item in multilingual["items"]] == ["aa000002"]


def test_list_q_matches_id_or_error_message(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    by_id = _body(_call(router, "GET", "/api/v1/traces?q=dead"))
    assert [item["id"] for item in by_id["items"]] == ["deadbeef"]

    by_message = _body(_call(router, "GET", "/api/v1/traces?q=bad"))
    assert [item["id"] for item in by_message["items"]] == ["aa000002"]

    # LIKE metacharacters are escaped: "%25" matches the literal percent sign only
    literal = _call(router, "GET", "/api/v1/traces?q=100%25")
    assert [item["id"] for item in _body(literal)["items"]] == ["deadbeef"]

    empty = _body(_call(router, "GET", "/api/v1/traces?q=refusal"))
    assert empty == {"items": [], "next_cursor": None, "total_estimate": 0}


def test_list_min_duration_and_tag(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    slow = _body(_call(router, "GET", "/api/v1/traces?min_duration_ms=700"))
    assert {item["id"] for item in slow["items"]} == {"aa000001", "deadbeef", "aa000004"}
    assert all(item["duration_ms"] >= 700 for item in slow["items"])

    bad = _call(router, "GET", "/api/v1/traces?min_duration_ms=fast")
    assert bad.status == 400
    assert _error(bad)["code"] == "invalid_filter"
    assert "min_duration_ms" in _error(bad)["message"]

    # exact JSON membership: "seed" must not match the element "seeded"
    assert [i["id"] for i in _body(_call(router, "GET", "/api/v1/traces?tag=seed"))["items"]] == [
        "aa000001"
    ]
    assert [i["id"] for i in _body(_call(router, "GET", "/api/v1/traces?tag=seeded"))["items"]] == [
        "aa000002"
    ]
    assert [i["id"] for i in _body(_call(router, "GET", "/api/v1/traces?tag=prod"))["items"]] == [
        "aa000004"
    ]


def test_list_since_until_and_range_windows(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    since = _body(_call(router, "GET", f"/api/v1/traces?since={fresh - 50}"))
    assert len(since["items"]) == 15  # everything except aa000003 (-100) and aa000004 (-10000)
    assert "aa000003" not in {item["id"] for item in since["items"]}

    until = _body(_call(router, "GET", f"/api/v1/traces?until={fresh - 50}"))
    assert [item["id"] for item in until["items"]] == ["aa000003", "aa000004"]

    windowed = _body(_call(router, "GET", "/api/v1/traces?range=15m"))
    assert len(windowed["items"]) == 16
    assert "aa000004" not in {item["id"] for item in windowed["items"]}

    conflict = _call(router, "GET", "/api/v1/traces?range=15m&since=100")
    assert conflict.status == 400
    assert _error(conflict)["code"] == "invalid_filter"
    assert "range" in _error(conflict)["message"]


def test_list_order_slowest(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    page = _body(_call(router, "GET", "/api/v1/traces?order=slowest"))
    assert page["items"][0]["id"] == "aa000004"  # 5000 ms beats the newest-but-fastest rows
    durations = [item["duration_ms"] for item in page["items"]]
    assert durations == sorted(durations, reverse=True)

    newest = _body(_call(router, "GET", "/api/v1/traces"))
    assert newest["items"][0]["id"] == "aa000001"  # the two orders disagree

    bad = _call(router, "GET", "/api/v1/traces?order=fastest")
    assert bad.status == 400
    assert _error(bad)["code"] == "invalid_filter"
    assert "order" in _error(bad)["message"]


def test_list_rejects_unknown_parameter(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    response = _call(router, "GET", "/api/v1/traces?bogus=1")
    assert response.status == 400
    error = _error(response)
    assert error["code"] == "invalid_filter"
    assert "bogus" in error["message"]


def test_list_rejects_limit_outside_bounds(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    for raw in ("201", "0", "abc"):
        response = _call(router, "GET", f"/api/v1/traces?limit={raw}")
        assert response.status == 400, raw
        assert _error(response)["code"] == "invalid_filter", raw
        assert "limit" in _error(response)["message"], raw


def test_list_status_range_limit_returns_only_500s_with_cursor(tmp_path: Path) -> None:
    fresh = time.time()
    router, _db_path = _build(tmp_path, lambda conn: _list_fixture(conn, fresh))

    response = _call(router, "GET", "/api/v1/traces?status=500&range=1h&limit=10")
    assert response.status == 200
    page = _body(response)
    # aa000004 sits outside the hour window, so the set is aa000001 plus the twelve e-rows
    assert page["total_estimate"] == 13
    assert len(page["items"]) == 10
    assert page["next_cursor"] is not None
    assert {item["status"] for item in page["items"]} == {500}
    assert [item["id"] for item in page["items"]] == ["aa000001"] + [
        f"{0xE0000000 + index:08x}" for index in range(9)
    ]


def test_list_cursor_continuation_has_no_duplicates_after_insert(tmp_path: Path) -> None:
    router, db_path = _build(tmp_path, lambda conn: seed_traces(conn, n=30, span_days=1.0))
    page1 = _body(_call(router, "GET", "/api/v1/traces?limit=10"))
    assert len(page1["items"]) == 10
    assert isinstance(page1["next_cursor"], str)

    # a newer trace lands between pages: keyset cursors must neither repeat nor backfill
    conn = connect(db_path)
    try:
        _insert_trace(conn, "ffffffff", time.time() + 10_000, duration=1.0)
        conn.commit()
    finally:
        conn.close()

    page2 = _body(
        _call(router, "GET", f"/api/v1/traces?limit=10&cursor={page1['next_cursor']}")
    )
    page3 = _body(
        _call(router, "GET", f"/api/v1/traces?limit=10&cursor={page2['next_cursor']}")
    )
    ids1 = [item["id"] for item in page1["items"]]
    ids2 = [item["id"] for item in page2["items"]]
    ids3 = [item["id"] for item in page3["items"]]
    assert not set(ids1) & set(ids2)
    assert not set(ids1 + ids2) & set(ids3)
    assert "ffffffff" not in ids1 + ids2 + ids3  # newer than the cursor: stays off-page
    assert page3["next_cursor"] is None
    assert set(ids1 + ids2 + ids3) == {f"{index:08x}" for index in range(30)}


# ---------------------------------------------------------------------------
# GET /api/v1/traces/{id} and .../observations
# ---------------------------------------------------------------------------


def test_detail_200_shape_and_summary_math(tmp_path: Path) -> None:
    router, _db_path = _build(tmp_path, _detail_fixture)
    response = _call(router, "GET", "/api/v1/traces/9f2c1a4b")

    assert response.status == 200
    payload = _body(response)
    assert set(payload) == {"trace", "observations", "scores", "logs", "summary"}

    trace = payload["trace"]
    assert trace["id"] == "9f2c1a4b"
    assert trace["tags"] == ["seed"]
    assert trace["meta"] == {"sampled": True}
    assert trace["duration_ms"] == 412.5

    # hand-computed: framework = 412.5 - 391.4 - 5.2 = 15.9;
    # forward_share = 391.4/412.5 = 0.9488..., queue_share = 5.2/412.5 = 0.0126...
    assert payload["summary"] == {
        "framework_ms": 15.9,
        "forward_ms": 391.4,
        "forward_share": 0.9488,
        "queue_share": 0.0126,
        "span_count": 3,
        "status_class": "5xx",
    }

    # waterfall order: start_ms ascending, id tiebreak (design-language 4.15)
    assert [o["id"] for o in payload["observations"]] == ["o1", "o2", "o3"]
    assert payload["observations"][1]["meta"] == {"depth": 2}
    assert [s["id"] for s in payload["scores"]] == ["s1"]
    assert [log["message"] for log in payload["logs"]] == ["second", "first"]  # newest first


def test_detail_unknown_trace_is_404_not_found(tmp_path: Path) -> None:
    router, _db_path = _build(tmp_path, _detail_fixture)
    response = _call(router, "GET", "/api/v1/traces/deadbeef")

    assert response.status == 404
    assert _error(response)["code"] == "not_found"
    assert response.headers["X-Request-Id"] == "a1b2c3d4"


def test_observations_keeps_spans_and_generations_in_waterfall_order(tmp_path: Path) -> None:
    router, _db_path = _build(tmp_path, _observations_fixture)
    response = _call(router, "GET", "/api/v1/traces/0b5e7a11/observations")

    assert response.status == 200
    payload = _body(response)
    assert set(payload) == {"items"}
    items = payload["items"]
    assert [item["id"] for item in items] == ["sp1", "gen", "sp2"]  # event "ev" excluded
    assert [item["start_ms"] for item in items] == [3.0, 8.0, 12.0]
    assert {item["type"] for item in items} == {"span", "generation"}
    assert set(items[0]) == {
        "id",
        "trace_id",
        "parent_id",
        "name",
        "type",
        "start_ms",
        "duration_ms",
        "status",
        "model",
        "input",
        "output",
        "meta",
    }

    missing = _call(router, "GET", "/api/v1/traces/ffff0000/observations")
    assert missing.status == 404
    assert _error(missing)["code"] == "not_found"


# ---------------------------------------------------------------------------
# POST /api/v1/traces/{id}/scores
# ---------------------------------------------------------------------------


def test_score_happy_path_stores_row_and_audits(tmp_path: Path) -> None:
    router, db_path = _build(tmp_path, _detail_fixture)
    response = _call(
        router,
        "POST",
        "/api/v1/traces/9f2c1a4b/scores",
        {"name": "helpful", "value": True, "comment": "clear refusal"},
    )

    assert response.status == 201
    item = _body(response)
    assert set(item) == {"id", "name", "value", "data_type", "source", "comment", "ts"}
    assert item["name"] == "helpful"
    assert item["value"] == 1.0  # stored REAL: bool True lands as 1.0
    assert item["data_type"] == "boolean"
    assert item["source"] == "api"
    assert item["comment"] == "clear refusal"
    assert isinstance(item["ts"], float) and abs(item["ts"] - time.time()) < 30

    stored = _rows(
        db_path,
        "SELECT trace_id, name, value, data_type, source, comment FROM scores WHERE id = ?",
        (item["id"],),
    )
    assert len(stored) == 1
    assert stored[0]["trace_id"] == "9f2c1a4b"
    assert stored[0]["value"] == 1.0
    assert stored[0]["data_type"] == "boolean"

    audit = _rows(db_path, "SELECT actor, action, target FROM audit_log ORDER BY id")
    assert [(row["actor"], row["action"], row["target"]) for row in audit] == [
        ("api", "trace.scored", "9f2c1a4b"),
    ]


def test_score_data_type_inference_for_bool_number_and_string(tmp_path: Path) -> None:
    router, _db_path = _build(tmp_path, _detail_fixture)
    cases = [
        ({"name": "rating", "value": 0.5}, "numeric", 0.5),
        ({"name": "ok", "value": True}, "boolean", 1.0),
        ({"name": "bucket", "value": "refund"}, "categorical", "refund"),
        ({"name": "explicit", "value": 1, "data_type": "boolean"}, "boolean", 1.0),
    ]
    for body, expected_type, expected_value in cases:
        response = _call(router, "POST", "/api/v1/traces/9f2c1a4b/scores", body)
        assert response.status == 201, body
        item = _body(response)
        assert item["data_type"] == expected_type, body
        assert item["value"] == expected_value, body
        assert item["source"] == "api" and item["comment"] is None, body


def test_score_rejections_are_400_invalid_request(tmp_path: Path) -> None:
    router, _db_path = _build(tmp_path, _detail_fixture)
    cases = [
        ({"value": 1.0}, "'name'"),
        ({"name": "", "value": 1.0}, "'name'"),
        ({"name": "x"}, "'value' is required"),
        ({"name": "x", "value": {"a": 1}}, "'value' must be"),
        ({"name": "x", "value": [1]}, "'value' must be"),
        ({"name": "x", "value": None}, "'value' must be"),
        ({"name": "x", "value": 1.0, "comment": "c" * 501}, "'comment'"),
        ({"name": "x", "value": 1.0, "data_type": "integer"}, "'data_type'"),
        ({"name": "x", "value": 1.0, "data_type": ["numeric"]}, "'data_type'"),
        ({"name": "x", "value": 1.0, "source": "playground"}, "'source'"),
    ]
    for body, needle in cases:
        response = _call(router, "POST", "/api/v1/traces/9f2c1a4b/scores", body)
        assert response.status == 400, body
        error = _error(response)
        assert error["code"] == "invalid_request", body
        assert needle in error["message"], body
        assert error["details"]["field"], body

    not_object = _call(router, "POST", "/api/v1/traces/9f2c1a4b/scores", [1, 2])
    assert _error(not_object)["code"] == "invalid_request"

    empty = _call(router, "POST", "/api/v1/traces/9f2c1a4b/scores")
    assert empty.status == 400
    assert _error(empty)["code"] == "invalid_json"

    # the comment cap is 500 inclusive
    accepted = _call(
        router,
        "POST",
        "/api/v1/traces/9f2c1a4b/scores",
        {"name": "note", "value": 2.0, "comment": "c" * 500},
    )
    assert accepted.status == 201


def test_score_unknown_trace_is_404_without_audit(tmp_path: Path) -> None:
    router, db_path = _build(tmp_path, _detail_fixture)
    response = _call(
        router, "POST", "/api/v1/traces/deadbeef/scores", {"name": "n", "value": 1.0}
    )

    assert response.status == 404
    assert _error(response)["code"] == "not_found"
    assert _rows(db_path, "SELECT id FROM audit_log") == []


# ---------------------------------------------------------------------------
# POST /api/v1/traces/{id}/tags
# ---------------------------------------------------------------------------


def test_tags_add_remove_combined_persist_and_audit(tmp_path: Path) -> None:
    router, db_path = _build(tmp_path, _tags_fixture)
    added = _call(router, "POST", "/api/v1/traces/0dd00007/tags", {"add": ["prod", "ui"]})
    assert added.status == 200
    assert _body(added) == {"tags": ["seed", "prod", "ui"]}

    removed = _call(router, "POST", "/api/v1/traces/0dd00007/tags",
                    {"remove": ["seed", "ghost"]})  # unknown removal is a no-op
    assert _body(removed) == {"tags": ["prod", "ui"]}

    combined = _call(router, "POST", "/api/v1/traces/0dd00007/tags",
                     {"add": ["api"], "remove": ["prod"]})
    assert _body(combined) == {"tags": ["ui", "api"]}

    # a fresh dispatch (fresh connection) reads back the persisted array
    detail = _call(router, "GET", "/api/v1/traces/0dd00007")
    assert _body(detail)["trace"]["tags"] == ["ui", "api"]

    audit = _rows(db_path, "SELECT actor, action, target FROM audit_log ORDER BY id")
    assert [(row["actor"], row["action"], row["target"]) for row in audit] == [
        ("api", "trace.tagged", "0dd00007"),
    ] * 3


def test_tags_enforce_model_limits(tmp_path: Path) -> None:
    router, db_path = _build(tmp_path, _tags_fixture)
    # seed + nine brings the trace to exactly ten tags
    filled = _call(router, "POST", "/api/v1/traces/0dd00007/tags",
                   {"add": [f"t{index}" for index in range(9)]})
    assert filled.status == 200
    assert len(_body(filled)["tags"]) == 10

    eleventh = _call(router, "POST", "/api/v1/traces/0dd00007/tags", {"add": ["eleventh"]})
    assert eleventh.status == 400
    error = _error(eleventh)
    assert error["code"] == "invalid_request"
    assert "10" in error["message"]

    over_long = _call(router, "POST", "/api/v1/traces/0dd00007/tags", {"add": ["x" * 33]})
    assert over_long.status == 400
    assert "32" in _error(over_long)["message"]

    not_a_string = _call(router, "POST", "/api/v1/traces/0dd00007/tags", {"add": [42]})
    assert not_a_string.status == 400
    assert _error(not_a_string)["code"] == "invalid_request"

    not_an_array = _call(router, "POST", "/api/v1/traces/0dd00007/tags", {"add": "seed"})
    assert not_an_array.status == 400
    assert _error(not_an_array)["code"] == "invalid_request"

    # the rejected writes left the stored array untouched
    detail = _call(router, "GET", "/api/v1/traces/0dd00007")
    assert _body(detail)["trace"]["tags"] == ["seed"] + [f"t{i}" for i in range(9)]
    assert len(_rows(db_path, "SELECT id FROM audit_log")) == 1  # only the accepted fill


def test_tags_unknown_trace_is_404(tmp_path: Path) -> None:
    router, _db_path = _build(tmp_path, _tags_fixture)
    response = _call(router, "POST", "/api/v1/traces/ffff0000/tags", {"add": ["x"]})

    assert response.status == 404
    assert _error(response)["code"] == "not_found"


# ---------------------------------------------------------------------------
# DELETE /api/v1/traces/{id}
# ---------------------------------------------------------------------------


def test_delete_removes_trace_cascades_and_audits(tmp_path: Path) -> None:
    router, db_path = _build(tmp_path, _delete_fixture)
    response = _call(router, "DELETE", "/api/v1/traces/dead00a1")

    assert response.status == 204
    assert response.body == b""
    assert _rows(db_path, "SELECT id FROM traces WHERE id = ?", ("dead00a1",)) == []
    assert _rows(db_path, "SELECT id FROM observations WHERE trace_id = ?", ("dead00a1",)) == []
    assert _rows(db_path, "SELECT id FROM scores WHERE trace_id = ?", ("dead00a1",)) == []
    # log_entry carries no foreign key: related lines survive (schema contract)
    logs = _rows(db_path, "SELECT message FROM log_entry WHERE trace_id = ?", ("dead00a1",))
    assert [row["message"] for row in logs] == ["kept line"]

    audit = _rows(db_path, "SELECT actor, action, target, meta FROM audit_log ORDER BY id")
    assert [(row["actor"], row["action"], row["target"]) for row in audit] == [
        ("api", "trace.deleted", "dead00a1"),
    ]
    assert json.loads(audit[0]["meta"]) == {"id": "dead00a1"}

    assert _call(router, "GET", "/api/v1/traces/dead00a1").status == 404

    second = _call(router, "DELETE", "/api/v1/traces/dead00a1")
    assert second.status == 404
    assert _error(second)["code"] == "not_found"
    assert len(_rows(db_path, "SELECT id FROM audit_log")) == 1  # the miss adds nothing


# ---------------------------------------------------------------------------
# Phase boundary: credentials arrive with Phase 3 roles
# ---------------------------------------------------------------------------


def test_every_endpoint_accepts_a_credential_less_request(tmp_path: Path) -> None:
    router, _db_path = _build(tmp_path, _detail_fixture)
    assert _call(router, "GET", "/api/v1/traces").status == 200
    assert _call(router, "GET", "/api/v1/traces/9f2c1a4b").status == 200
    assert _call(router, "GET", "/api/v1/traces/9f2c1a4b/observations").status == 200
    scored = _call(router, "POST", "/api/v1/traces/9f2c1a4b/scores", {"name": "n", "value": 1.0})
    assert scored.status == 201
    tagged = _call(router, "POST", "/api/v1/traces/9f2c1a4b/tags", {"add": ["free"]})
    assert tagged.status == 200
    assert _call(router, "DELETE", "/api/v1/traces/9f2c1a4b").status == 204
