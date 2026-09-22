"""Logs API contract: filter matrix, cursor paging and the JSONL export attachment."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from layawatch.api.logs import add_logs_routes
from layawatch.http.router import Router
from layawatch.http.types import Request, Response
from layawatch.store import db

#: Frozen epoch every seeded timestamp and range window is derived from.
NOW = 1_790_000_000.0


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", lambda: NOW)


def _build(tmp_path: Path, *, seed: bool = True) -> Router:
    db_path = tmp_path / "state.sqlite3"
    conn = db.connect(db_path)
    db.migrate(conn)
    if seed:
        rows = [
            (NOW - 1, "info", "server", "aa000001", "request done in 12ms"),
            (NOW - 2, "warning", "server", "aa000001", "slow request warning"),
            (NOW - 3, "error", "server", None, "upstream failed: boom"),
            (NOW - 4, "debug", "server", "bb000002", "verbose detail"),
            (NOW - 4000, "info", "server", None, "older than one hour"),
        ]
        conn.executemany(
            "INSERT INTO log_entry (ts, level, source, trace_id, message) VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
    conn.close()
    router = Router()
    add_logs_routes(router, db_path)
    return router


def call(router: Router, target: str) -> Response:
    return router.dispatch(Request.build("GET", target, {}, b"", "feedbeef", "127.0.0.1"))


def payload_of(response: Response) -> dict:
    return json.loads(response.body)


def error_of(response: Response) -> dict:
    return payload_of(response)["error"]


def messages(page: dict) -> list[str]:
    return [item["message"] for item in page["items"]]


def lines_of(body: bytes) -> list[str]:
    return [line for line in body.decode("utf-8").split("\n") if line]


# ---------------------------------------------------------------------------
# filter matrix
# ---------------------------------------------------------------------------


def test_level_filter_and_warn_alias(tmp_path: Path) -> None:
    router = _build(tmp_path)
    aliased = payload_of(call(router, "/api/v1/logs?level=warn"))
    assert messages(aliased) == ["slow request warning"]
    errors = payload_of(call(router, "/api/v1/logs?level=error"))
    assert messages(errors) == ["upstream failed: boom"]


def test_q_matches_message_substring(tmp_path: Path) -> None:
    router = _build(tmp_path)
    hit = payload_of(call(router, "/api/v1/logs?q=upstream"))
    assert messages(hit) == ["upstream failed: boom"]
    both = payload_of(call(router, "/api/v1/logs?q=request"))
    assert messages(both) == ["request done in 12ms", "slow request warning"]
    none = payload_of(call(router, "/api/v1/logs?q=nothing-matches"))
    assert none["items"] == []
    assert none["total_estimate"] == 0


def test_trace_id_filter(tmp_path: Path) -> None:
    router = _build(tmp_path)
    page = payload_of(call(router, "/api/v1/logs?trace_id=aa000001"))
    assert messages(page) == ["request done in 12ms", "slow request warning"]


def test_range_filter_keeps_only_the_window(tmp_path: Path) -> None:
    router = _build(tmp_path)
    hour = payload_of(call(router, "/api/v1/logs?range=1h"))
    assert len(hour["items"]) == 4  # the NOW-4000 line falls outside one hour
    six_hours = payload_of(call(router, "/api/v1/logs?range=6h"))
    assert len(six_hours["items"]) == 5


def test_invalid_filters_name_the_parameter(tmp_path: Path) -> None:
    router = _build(tmp_path)
    bogus = call(router, "/api/v1/logs?bogus=1")
    assert bogus.status == 400
    assert error_of(bogus)["code"] == "invalid_filter"
    assert "bogus" in error_of(bogus)["message"]
    level = call(router, "/api/v1/logs?level=critical")
    assert level.status == 400
    assert "level" in error_of(level)["message"]
    conflict = call(router, "/api/v1/logs?range=15m&since=100")
    assert conflict.status == 400
    assert "since" in error_of(conflict)["message"]


# ---------------------------------------------------------------------------
# envelope and cursor continuation
# ---------------------------------------------------------------------------


def test_envelope_shape_and_cursor_pages_without_overlap(tmp_path: Path) -> None:
    router = _build(tmp_path)
    page1 = payload_of(call(router, "/api/v1/logs?limit=2"))
    assert set(page1) == {"items", "next_cursor", "total_estimate"}
    assert page1["total_estimate"] == 5
    assert set(page1["items"][0]) == {"id", "ts", "level", "source", "trace_id", "message"}
    assert messages(page1) == ["request done in 12ms", "slow request warning"]
    assert page1["next_cursor"]

    cursor2 = page1["next_cursor"]
    page2 = payload_of(call(router, f"/api/v1/logs?limit=2&cursor={cursor2}"))
    assert messages(page2) == ["upstream failed: boom", "verbose detail"]
    page3 = payload_of(call(router, f"/api/v1/logs?limit=2&cursor={page2['next_cursor']}"))
    assert messages(page3) == ["older than one hour"]
    assert page3["next_cursor"] is None

    ids = [item["id"] for page in (page1, page2, page3) for item in page["items"]]
    assert len(ids) == len(set(ids)) == 5  # no overlap, no gap


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_default_is_a_one_hour_jsonl_attachment(tmp_path: Path) -> None:
    router = _build(tmp_path)
    response = call(router, "/api/v1/logs/export?format=jsonl")
    assert response.status == 200
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert response.headers["Content-Disposition"] == "attachment; filename=logs-1h.jsonl"
    raw = response.body.decode("utf-8")
    assert raw.endswith("\n")  # one JSON object per line, newline-terminated
    rows = [json.loads(line) for line in lines_of(response.body)]
    assert len(rows) == 4  # default range=1h excludes the NOW-4000 line
    assert [row["message"] for row in rows] == [
        "request done in 12ms",
        "slow request warning",
        "upstream failed: boom",
        "verbose detail",
    ]
    assert set(rows[0]) == {"id", "ts", "level", "source", "trace_id", "message"}


def test_export_range_widens_window_and_filename(tmp_path: Path) -> None:
    router = _build(tmp_path)
    response = call(router, "/api/v1/logs/export?format=jsonl&range=6h")
    assert response.status == 200
    assert response.headers["Content-Disposition"] == "attachment; filename=logs-6h.jsonl"
    assert len(lines_of(response.body)) == 5


@pytest.mark.parametrize(
    ("target", "named"),
    [
        ("/api/v1/logs/export?format=csv", "format"),
        ("/api/v1/logs/export", "format"),  # format is required, only jsonl exists
        ("/api/v1/logs/export?format=jsonl&range=9h", "range"),
        ("/api/v1/logs/export?format=jsonl&bogus=1", "bogus"),
    ],
)
def test_export_rejects_bad_parameters(tmp_path: Path, target: str, named: str) -> None:
    router = _build(tmp_path)
    response = call(router, target)
    assert response.status == 400
    error = error_of(response)
    assert error["code"] == "invalid_filter"
    assert named in error["message"]


def test_export_of_an_empty_database_is_an_empty_200_body(tmp_path: Path) -> None:
    router = _build(tmp_path, seed=False)
    response = call(router, "/api/v1/logs/export?format=jsonl")
    assert response.status == 200
    assert response.body == b""
