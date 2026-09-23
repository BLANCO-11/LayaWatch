#!/usr/bin/env python
"""End-to-end operator path smoke test (plan phase-8 task 13).

Against a RUNNING instance (fresh container or local) this walks the full operator
path with ``urllib`` and a cookie jar only:

  healthz, first-run setup (fresh instance), wrong password -> 401 + audit entry,
  login, create an API key, arm key auth, POST /predict with a fixed English
  fixture, poll GET /api/v1/traces/{id} until the trace and its spans are visible,
  model load, model unload, logout.

Every step asserts its status code and JSON shape, prints a step-by-step summary,
and the run exits non-zero on the first failure. Usage::

    python scripts/e2e_smoke.py --base-url http://127.0.0.1:8050
    python scripts/e2e_smoke.py --email me@example.com --password 'existing-pw'
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request

DEFAULT_EMAIL = "e2e@layawatch.local"
DEFAULT_PASSWORD = "e2e-smoke-passw0rd"  # >= 12 chars, not the email local part
DEFAULT_NAME = "E2E Operator"

#: Fixed English fixture: the documented smoke input (scripts/smoke_laya.py), valid for
#: both the real laya engine (question payloads need ``type``) and the engineless fake.
FIXTURE = {
    "state": {
        "from": "user@acme.com",
        "subject": "Duplicate charge on invoice #4411",
        "body": "Hi, we were billed twice for March. Please refund the duplicate today "
        "or we will cancel our plan.",
    },
    "questions": {
        "department": {
            "type": "choice",
            "instructions": "Which department should handle this request?",
            "criteria": {
                "billing": "invoices, payments, refunds",
                "technical": "bugs, outages, system errors",
                "sales": "pricing, new contracts",
                "other": "everything else",
            },
        },
        "churn_risk": {
            "type": "noul",
            "instructions": "Does the user threaten to cancel or leave?",
        },
    },
}

TRACE_TIMEOUT_S = 30.0
TRACE_POLL_S = 0.25
#: Generous per-request ceilings: the first predict or model load may build a checkpoint.
IO_TIMEOUT_S = 30.0
ENGINE_TIMEOUT_S = 180.0
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SETUP_CLOSED = "setup_closed"
#: Engineless and engine runs both record these middleware spans on every /predict.
EXPECTED_SPANS = ("http.receive", "response.send")


class Fail(Exception):
    """A step failed; the runner prints the summary and exits 1."""


class Client:
    """urllib client with a cookie jar and automatic CSRF echo for session mutations."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = IO_TIMEOUT_S,
    ) -> tuple[int, dict | None, dict[str, str]]:
        """``(status, json-body-or-None, response-headers)``; raises Fail when unreachable."""
        url = self.base + path
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        csrf = self._cookie("lw_csrf")
        if method in MUTATING and csrf:
            hdrs.setdefault("X-CSRF-Token", csrf)
        request = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                status, raw = response.status, response.read()
                response_headers = dict(response.headers)
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
            response_headers = dict(exc.headers)
        except (urllib.error.URLError, OSError) as exc:
            raise Fail(f"{method} {path}: cannot reach {url}: {exc}") from exc
        payload = None
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
        return status, payload, response_headers

    def _cookie(self, name: str) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return cookie.value
        return None


class Runner:
    """Step ledger: prints as it goes, keeps the summary for the failure path."""

    def __init__(self) -> None:
        self.done: list[str] = []

    def ok(self, label: str, detail: str = "") -> None:
        line = f"ok   {label}" + (f" - {detail}" if detail else "")
        self.done.append(line)
        print(f"[e2e] {line}", flush=True)

    def die(self, label: str, detail: str) -> None:
        print(f"[e2e] FAIL {label} - {detail}", flush=True)
        self.summary()
        raise SystemExit(1)

    def summary(self) -> None:
        print(f"[e2e] summary: {len(self.done)} step(s) passed")
        for line in self.done:
            print(f"[e2e]   {line}")


def expect_json(
    runner: Runner, label: str, status: int, payload: dict | None, want: int
) -> dict:
    if status != want:
        runner.die(label, f"status {status}, wanted {want}; body={payload!r}")
    if not isinstance(payload, dict):
        runner.die(label, f"expected a JSON object, got {payload!r}")
    return payload


def run(args: argparse.Namespace) -> None:
    runner = Runner()
    client = Client(args.base_url)

    # 1. Reachability.
    status, payload, _raw = client.request("GET", "/healthz")
    body = expect_json(runner, "GET /healthz", status, payload, 200)
    runner.ok("healthz", f"status={body.get('status')}")

    # 2. First-run setup on a fresh instance; already-initialized instances log in.
    status, payload, _raw = client.request(
        "POST",
        "/api/v1/auth/setup",
        {"email": args.email, "name": DEFAULT_NAME, "password": args.password},
    )
    if status == 200:
        expect_json(runner, "POST /api/v1/auth/setup", status, payload, 200)
        runner.ok("first-run setup", f"owner {args.email} created")
    elif status == 403 and isinstance(payload, dict) and payload.get("error", {}).get(
        "code"
    ) == SETUP_CLOSED:
        runner.ok("first-run setup", "instance already initialized; logging in")
    else:
        runner.die("POST /api/v1/auth/setup", f"status {status}; body={payload!r}")

    # 3. Negative: wrong password is 401 and leaves an audit entry.
    status, payload, _raw = client.request(
        "POST",
        "/api/v1/auth/login",
        {"email": args.email, "password": f"wrong-{args.password}"},
    )
    error = expect_json(runner, "login with wrong password", status, payload, 401)
    if error.get("error", {}).get("code") != "missing_or_invalid_credential":
        runner.die("login with wrong password", f"unexpected envelope {error!r}")
    runner.ok("wrong password rejected", "401 missing_or_invalid_credential")

    # 4. Real login: session + CSRF cookies land in the jar.
    status, payload, _raw = client.request(
        "POST", "/api/v1/auth/login", {"email": args.email, "password": args.password}
    )
    me = expect_json(runner, "POST /api/v1/auth/login", status, payload, 200)
    if me.get("email") != args.email:
        runner.die("POST /api/v1/auth/login", f"unexpected me payload {me!r}")
    if client._cookie("lw_session") is None or client._cookie("lw_csrf") is None:
        runner.die("POST /api/v1/auth/login", "session/CSRF cookies missing from the jar")
    runner.ok("login", f"session cookie issued for {args.email}")

    # 5. The failed attempt is in the audit log.
    status, payload, _raw = client.request(
        "GET", "/api/v1/audit?action=auth.login_failed&limit=5"
    )
    page = expect_json(runner, "GET /api/v1/audit (login_failed)", status, payload, 200)
    items = page.get("items")
    if not isinstance(items, list) or not any(
        item.get("actor") == args.email for item in items
    ):
        runner.die("GET /api/v1/audit (login_failed)", f"no entry for {args.email}: {page!r}")
    runner.ok("failed login audited", "auth.login_failed present")

    # 6. Create an API key (secret shown once).
    status, payload, _raw = client.request(
        "POST", "/api/v1/keys", {"name": "e2e-smoke"}
    )
    key = expect_json(runner, "POST /api/v1/keys", status, payload, 200)
    secret = key.get("key")
    if not isinstance(secret, str) or not secret.startswith("lay_"):
        runner.die("POST /api/v1/keys", f"no lay_ secret in {key!r}")
    if not isinstance(key.get("id"), str) or not key.get("prefix"):
        runner.die("POST /api/v1/keys", f"missing id/prefix in {key!r}")
    runner.ok("api key created", f"id={key['id']} prefix={key['prefix']}")

    # 7. Arm key auth for /predict and /route.
    status, payload, _raw = client.request(
        "POST", "/api/v1/keys/auth", {"enabled": True}
    )
    armed = expect_json(runner, "POST /api/v1/keys/auth", status, payload, 200)
    if armed.get("enabled") is not True:
        runner.die("POST /api/v1/keys/auth", f"not armed: {armed!r}")
    status, payload, _raw = client.request("GET", "/api/v1/keys/auth")
    armed = expect_json(runner, "GET /api/v1/keys/auth", status, payload, 200)
    if armed.get("enabled") is not True:
        runner.die("GET /api/v1/keys/auth", f"armed state not persisted: {armed!r}")
    runner.ok("key auth armed", "GET confirms enabled=true")

    # 8. Predict with the fixed English fixture; the request id IS the trace id
    #    (api-reference section 2, engine.py contract).
    status, payload, response_headers = client.request(
        "POST",
        "/predict",
        FIXTURE,
        headers={"X-API-Key": secret},
        timeout=ENGINE_TIMEOUT_S,
    )
    answer = expect_json(runner, "POST /predict", status, payload, 200)
    if not isinstance(answer.get("answers"), dict) or not answer.get("model"):
        runner.die("POST /predict", f"missing answers/model in {answer!r}")
    trace_id = next(
        (value for key, value in response_headers.items() if key.lower() == "x-request-id"),
        "",
    )
    if not re.fullmatch(r"[0-9a-f]{8}", trace_id):
        runner.die("POST /predict", f"X-Request-Id is not an 8-hex trace id: {trace_id!r}")
    runner.ok(
        "predict",
        f"model={answer['model']} route={answer.get('route_reason')} trace={trace_id}",
    )

    # 9. Poll the trace until it and its spans are visible.
    trace = poll_trace(client, trace_id, runner)
    names = [obs.get("name") for obs in trace.get("observations", [])]
    missing = [name for name in EXPECTED_SPANS if name not in names]
    if missing:
        runner.die("trace spans", f"missing {missing} in {names!r}")
    runner.ok(
        "trace visible",
        f"trace={trace_id} spans={len(names)} ({', '.join(sorted(set(names)))})",
    )

    # 10. Model load (one retry only, per the phase-8 risk table) and unload.
    status, payload, _raw = client.request(
        "POST", "/api/v1/models/load", {"models": ["english"]}, timeout=ENGINE_TIMEOUT_S
    )
    if status != 200:  # one retry only
        status, payload, _raw = client.request(
            "POST", "/api/v1/models/load", {"models": ["english"]}, timeout=ENGINE_TIMEOUT_S
        )
    loaded = expect_json(runner, "POST /api/v1/models/load", status, payload, 200)
    if "english" not in (loaded.get("loaded") or []):
        runner.die("POST /api/v1/models/load", f"english not loaded: {loaded!r}")
    runner.ok("model load", f"loaded={loaded['loaded']}")

    status, payload, _raw = client.request(
        "POST", "/api/v1/models/unload", {"models": ["english"]}
    )
    unloaded = expect_json(runner, "POST /api/v1/models/unload", status, payload, 200)
    if "english" in (unloaded.get("loaded") or []):
        runner.die("POST /api/v1/models/unload", f"still loaded: {unloaded!r}")
    runner.ok("model unload", f"loaded={unloaded['loaded']}")

    # 11. Logout kills the session.
    status, payload, _raw = client.request("POST", "/api/v1/auth/logout")
    expect_json(runner, "POST /api/v1/auth/logout", status, payload, 200)
    status, payload, _raw = client.request("GET", "/api/v1/auth/me")
    expect_json(runner, "GET /api/v1/auth/me after logout", status, payload, 401)
    runner.ok("logout", "session revoked; /auth/me now 401")

    runner.summary()
    print("[e2e] PASS")


def poll_trace(client: Client, trace_id: str, runner: Runner) -> dict:
    """Poll GET /api/v1/traces/{id} until 200 with observations (writer batch cadence)."""
    deadline = time.monotonic() + TRACE_TIMEOUT_S
    last = "no attempt"
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        status, payload, _raw = client.request("GET", f"/api/v1/traces/{trace_id}")
        if status == 200 and isinstance(payload, dict):
            observations = payload.get("observations")
            if isinstance(observations, list) and observations:
                return payload
            last = "200 but no observations yet"
        elif status == 404:
            last = "404 not flushed yet"
        else:
            last = f"status {status}: {payload!r}"
        if attempts % 8 == 0:
            print(f"[e2e]   ... waiting for trace {trace_id} ({last})", flush=True)
        time.sleep(TRACE_POLL_S)
    runner.die(
        "GET /api/v1/traces/{id}",
        f"trace {trace_id} not visible with spans after {TRACE_TIMEOUT_S:g}s ({last})",
    )
    raise AssertionError("unreachable")  # die() exits


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8050")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        run(args)
    except Fail as exc:
        print(f"[e2e] FAIL {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
