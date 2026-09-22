"""Instrumented request lifecycle: span order, auth, error mapping, trace closure."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request

from layawatch.api.engine import add_engine_routes
from layawatch.config import Config
from layawatch.engine.adapter import EnglishOnlyError, FakeAdapter
from layawatch.engine.keys import generate_secret, hash_secret, load_pepper, prefix_of
from layawatch.http.middleware import ThreadLocalSpans, instrument
from layawatch.http.router import Router
from layawatch.http.server import build_server
from layawatch.http.types import Request, json_response
from layawatch.obs.recorder import Recorder, Trace
from layawatch.obs.vocabulary import SPAN_NAMES, validate
from layawatch.store.db import connect, migrate

BODY = {"state": {"dept": "billing"}, "questions": {"churn": {"choice": "yes"}}}
BODY_BYTES = len(json.dumps(BODY).encode("utf-8"))
KEY_ID = "k_test0001"


def make_request(
    method: str = "POST",
    target: str = "/predict",
    body: dict | bytes | None = None,
    headers: dict[str, str] | None = None,
    request_id: str = "a1b2c3d4",
) -> Request:
    if isinstance(body, bytes):
        raw = body
    elif body is None:
        raw = json.dumps(BODY).encode("utf-8")
    else:
        raw = json.dumps(body).encode("utf-8")
    lowered = {name.lower(): value for name, value in (headers or {}).items()}
    return Request.build(method, target, lowered, raw, request_id, "127.0.0.1")


def build(tmp_path, adapter, **instrument_kwargs):
    db_path = tmp_path / "state.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    recorder = Recorder()
    router = Router()
    add_engine_routes(router, adapter)
    router.add("GET", "/healthz", lambda _req: json_response({"status": "ok"}))
    handle = instrument(router, recorder, db_path, **instrument_kwargs)
    return handle, recorder, router, db_path


def arm(db_path, state_dir) -> str:
    """Insert a real key row plus the settings toggle that arms key auth."""
    secret = generate_secret()
    pepper = load_pepper(state_dir)
    conn = connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO api_keys (id, name, prefix, hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (KEY_ID, "test key", prefix_of(secret), hash_secret(secret, pepper), time.time()),
        )
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('keys.auth', 'true', ?)",
            (time.time(),),
        )
    conn.close()
    return secret


def observations(trace: Trace, name: str):
    return [obs for obs in trace.observations if obs.name == name]


def assert_vocabulary(trace: Trace) -> None:
    for obs in trace.observations:
        assert obs.name in SPAN_NAMES, obs.name
        validate(obs.name, obs.meta or {})  # re-runs the loud gate over everything stored


def test_unarmed_predict_records_documented_span_order(tmp_path) -> None:
    handle, recorder, _router, _db = build(tmp_path, FakeAdapter())
    request = make_request()
    response = handle(request)

    assert response.status == 200
    trace = recorder.ring_traces()[-1]
    names = [obs.name for obs in trace.observations]
    print(f"span order: {names}")
    assert names == ["http.receive", "auth.verify", "body.parse", "response.send"]
    assert_vocabulary(trace)

    receive = observations(trace, "http.receive")[0]
    assert receive.meta == {
        "method": "POST",
        "path": "/predict",
        "remote": "127.0.0.1",
        "content_length": BODY_BYTES,
    }
    auth = observations(trace, "auth.verify")[0]
    assert auth.meta == {"scheme": "none", "ok": True}
    parsed = observations(trace, "body.parse")[0]
    assert parsed.meta == {"state_bytes": BODY_BYTES, "question_count": 1}
    sent = observations(trace, "response.send")[0]
    assert sent.meta == {"status": 200, "bytes": len(response.body)}
    assert sent.duration_ms >= 0

    assert trace.status == 200
    assert trace.id == request.request_id
    assert trace.route == "/predict"
    assert trace.question_count == 1
    assert trace.state_bytes == BODY_BYTES
    assert trace.model == "english"
    assert trace.route_reason == "state is English"
    assert trace.lang == "en"
    assert trace.meta["armed"] is False
    assert trace.error_code is None
    assert response.headers["X-Request-Id"] == trace.id

    payload = json.loads(response.body)
    assert payload["model"] == "english"
    assert set(payload["answers"]) == {"churn"}


def test_non_engine_path_is_pure_passthrough(tmp_path) -> None:
    handle, recorder, _router, _db = build(tmp_path, FakeAdapter())
    assert recorder.ring_traces() == []
    response = handle(make_request("GET", "/healthz", body=b""))
    assert response.status == 200
    assert json.loads(response.body) == {"status": "ok"}
    assert recorder.ring_traces() == []


def test_missing_key_returns_401_envelope_and_error_trace(tmp_path) -> None:
    handle, recorder, _router, db = build(tmp_path, FakeAdapter())
    secret = arm(db, tmp_path)
    response = handle(make_request())  # no credential presented

    assert response.status == 401
    error = json.loads(response.body)["error"]
    assert error["code"] == "missing_or_invalid_credential"
    assert response.headers["X-Request-Id"] == recorder.ring_traces()[-1].id

    trace = recorder.ring_traces()[-1]
    names = [obs.name for obs in trace.observations]
    assert names == ["http.receive", "auth.verify", "error", "response.send"]
    assert "forward" not in names
    auth = observations(trace, "auth.verify")[0]
    assert auth.status == "error"
    assert auth.meta == {"scheme": "none", "ok": False}
    failures = observations(trace, "error")
    assert len(failures) == 1
    assert failures[0].status == "error"
    assert failures[0].meta == {
        "code": "missing_or_invalid_credential",
        "message": "missing or invalid credential",
        "where": "auth.verify",
    }
    assert trace.status == 401
    assert trace.error_code == "missing_or_invalid_credential"
    assert trace.meta["armed"] is True
    assert secret not in repr(trace)
    assert_vocabulary(trace)


def test_wrong_key_is_rejected_without_linking_the_trace(tmp_path) -> None:
    handle, recorder, _router, db = build(tmp_path, FakeAdapter())
    secret = arm(db, tmp_path)
    wrong = secret[:-1] + ("A" if secret[-1] != "A" else "B")  # same prefix, bad digest
    response = handle(make_request(headers={"X-API-Key": wrong}))

    assert response.status == 401
    trace = recorder.ring_traces()[-1]
    assert trace.status == 401
    auth = observations(trace, "auth.verify")[0]
    assert auth.meta == {"scheme": "api_key", "ok": False}
    assert "key_id" not in auth.meta
    assert trace.client_key_id is None
    assert wrong not in repr(trace)


def test_valid_x_api_key_passes_and_links_the_trace(tmp_path) -> None:
    handle, recorder, _router, db = build(tmp_path, FakeAdapter())
    secret = arm(db, tmp_path)
    response = handle(make_request(headers={"X-API-Key": secret}))

    assert response.status == 200
    trace = recorder.ring_traces()[-1]
    assert trace.status == 200
    auth = observations(trace, "auth.verify")[0]
    assert auth.meta == {"scheme": "api_key", "key_id": KEY_ID, "ok": True}
    assert trace.client_key_id == KEY_ID
    assert trace.meta["armed"] is True
    assert secret not in repr(trace)
    assert secret not in json.dumps(trace.meta)
    assert_vocabulary(trace)


def test_valid_bearer_token_passes(tmp_path) -> None:
    handle, recorder, _router, db = build(tmp_path, FakeAdapter())
    secret = arm(db, tmp_path)
    response = handle(make_request(headers={"Authorization": f"Bearer {secret}"}))

    assert response.status == 200
    auth = observations(recorder.ring_traces()[-1], "auth.verify")[0]
    assert auth.meta == {"scheme": "api_key", "key_id": KEY_ID, "ok": True}


def test_invalid_json_marks_body_parse_and_returns_400(tmp_path) -> None:
    handle, recorder, _router, _db = build(tmp_path, FakeAdapter())
    response = handle(make_request(body=b'{"state": '))

    assert response.status == 400
    assert json.loads(response.body)["error"]["code"] == "invalid_json"
    trace = recorder.ring_traces()[-1]
    names = [obs.name for obs in trace.observations]
    assert names == ["http.receive", "auth.verify", "body.parse", "error", "response.send"]
    assert observations(trace, "body.parse")[0].status == "error"
    failures = observations(trace, "error")
    assert failures[0].meta["where"] == "body.parse"
    assert trace.status == 400
    assert trace.error_code == "invalid_json"
    assert_vocabulary(trace)


def test_engine_rejection_maps_to_422_without_forward_span(tmp_path) -> None:
    refusal = EnglishOnlyError(
        "english-only deployment: state did not detect as English",
        detection={"script": "Latin", "language": None},
    )
    adapter = FakeAdapter(error=refusal, fail_times=1)
    handle, recorder, _router, _db = build(tmp_path, adapter)
    response = handle(make_request())

    assert response.status == 422
    error = json.loads(response.body)["error"]
    assert error["code"] == "english_only"
    assert error["details"]["detection"]["script"] == "Latin"
    trace = recorder.ring_traces()[-1]
    names = [obs.name for obs in trace.observations]
    assert "forward" not in names
    assert "queue.wait" not in names
    assert names[-1] == "response.send"
    failures = observations(trace, "error")
    assert failures[0].meta == {
        "code": "english_only",
        "message": "english-only deployment: state did not detect as English",
        "where": "handler",
    }
    assert trace.status == 422
    assert trace.error_code == "english_only"
    assert_vocabulary(trace)


def test_unexpected_adapter_error_returns_500_without_leaking_text(tmp_path) -> None:
    adapter = FakeAdapter(error=RuntimeError("kaboom: db password=hunter2"), fail_times=1)
    handle, recorder, _router, _db = build(tmp_path, adapter)
    response = handle(make_request())

    assert response.status == 500
    error = json.loads(response.body)["error"]
    assert error == {"code": "internal_error", "message": "internal server error"}
    assert b"hunter2" not in response.body
    trace = recorder.ring_traces()[-1]
    assert trace.status == 500
    assert trace.error_code == "internal_error"
    failures = observations(trace, "error")
    assert failures[0].meta["code"] == "internal_error"
    assert failures[0].meta["where"] == "handler"
    assert_vocabulary(trace)


def test_broken_state_db_still_records_a_500_trace(tmp_path) -> None:
    recorder = Recorder()
    router = Router()
    add_engine_routes(router, FakeAdapter())
    handle = instrument(router, recorder, tmp_path / "missing" / "state.sqlite3")
    response = handle(make_request())  # connect() fails inside auth.verify

    assert response.status == 500
    assert json.loads(response.body)["error"]["code"] == "internal_error"
    trace = recorder.ring_traces()[-1]
    assert trace.status == 500
    assert trace.error_code == "internal_error"
    assert "unable to open" in trace.error_message
    failures = observations(trace, "error")
    assert failures[0].meta["where"] == "auth.verify"
    assert [obs.name for obs in trace.observations][-1] == "response.send"


def test_close_on_sent_defers_trace_closure_until_the_hook_runs(tmp_path) -> None:
    handle, recorder, _router, _db = build(tmp_path, FakeAdapter(), close_on_sent=True)
    response = handle(make_request())

    assert response.status == 200
    assert recorder.ring_traces() == []  # deliberately still open: spans the socket write
    assert callable(response.on_sent)
    response.on_sent()
    response.on_sent()  # idempotent: close + finish are both single-shot
    traces = recorder.ring_traces()
    assert len(traces) == 1
    assert traces[0].status == 200
    assert [obs.name for obs in traces[0].observations][-1] == "response.send"


def test_server_write_path_invokes_on_sent_and_closes_the_trace(tmp_path) -> None:
    secret_socket = socket.socket()
    secret_socket.bind(("127.0.0.1", 0))
    port = secret_socket.getsockname()[1]
    secret_socket.close()

    handle, recorder, router, _db = build(tmp_path, FakeAdapter(), close_on_sent=True)
    config = Config.load({"LAYA_STATE_DIR": str(tmp_path), "LAYA_PORT": str(port)})
    server = build_server(config, router, dispatch=handle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/predict",
            data=json.dumps(BODY).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            assert resp.status == 200
            request_id = resp.headers["X-Request-Id"]
        deadline = time.time() + 2
        while time.time() < deadline and not recorder.ring_traces():
            time.sleep(0.01)  # the hook runs just after the client sees the body
        traces = recorder.ring_traces()
        assert traces, "server write path never invoked response.on_sent"
        trace = traces[-1]
        assert trace.id == request_id
        assert trace.status == 200
        assert [obs.name for obs in trace.observations][-1] == "response.send"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_thread_local_spans_delegate_inside_a_trace_and_noop_outside(tmp_path) -> None:
    recorder = Recorder()
    proxy = ThreadLocalSpans(recorder)
    assert proxy.recorder is recorder

    # Outside a trace: every call is a no-op and nothing enters the ring.
    with proxy.span("queue.wait", depth_at_acquire=0) as entered:
        assert entered is not None
    assert proxy.event("error", code="x", message="y", where="z") is None
    proxy.set(model="english")
    assert recorder.ring_traces() == []

    # Inside a trace: spans and fields land in the active context (the adapter's view).
    ctx = recorder.start(route="/predict", method="POST", request_id="0badc0de")
    with proxy.span("queue.wait", depth_at_acquire=1):
        pass
    proxy.set(queue_ms=1.5)
    ctx.finish(200)

    trace = recorder.ring_traces()[-1]
    assert trace.id == "0badc0de"
    assert [obs.name for obs in trace.observations] == ["queue.wait"]
    assert trace.observations[0].meta == {"depth_at_acquire": 1}
    assert trace.queue_ms == 1.5
    assert_vocabulary(trace)
