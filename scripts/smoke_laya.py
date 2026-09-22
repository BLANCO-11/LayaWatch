"""Smoke test: proves a local CPU deployment of laya actually works.

Exercises the two load paths and one routed request, then reports wall-clock
latency so CPU hosting expectations are grounded in measurement, not README claims.

  .venv/bin/python scripts/smoke_laya.py            # direct single-checkpoint load
  .venv/bin/python scripts/smoke_laya.py --router   # Router path (downloads both checkpoints)

After the engine checks it spawns a local layawatch server against a fresh temp state dir,
POSTs /predict once, and asserts the persisted trace (Phase 1 task 12):

  .venv/bin/python scripts/smoke_laya.py --skip-http              # engine checks only
  .venv/bin/python scripts/smoke_laya.py --http-port 8090 --keep-state
"""
import argparse
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

STATE = {
    "from": "user@acme.com",
    "subject": "Duplicate charge on invoice #4411",
    "body": "Hi, we were billed twice for March. Please refund the duplicate today "
            "or we will cancel our plan.",
}
QUESTIONS = {
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
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this request?",
        "criteria": ["not urgent", "soon", "critical deadline or blocking issue"],
    },
    "churn_risk": {
        "type": "noul",
        "instructions": "Does the user threaten to cancel or leave?",
    },
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTHZ_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 10.0
TRACE_WAIT_S = 5.0  # the writer batches traces for up to 250 ms before they hit SQLite
PREDICT_TIMEOUT_S = 60.0


def check(result, expect_choice=None):
    answers = result["answers"]
    assert set(answers) == set(QUESTIONS), answers.keys()
    dept = answers["department"]
    assert dept["choice"] in QUESTIONS["department"]["criteria"], dept
    assert 0.0 <= dept["confidence"] <= 1.0, dept
    noul = answers["churn_risk"]["noul"]
    assert 0.0 <= noul <= 1.0, noul
    if expect_choice:
        assert dept["choice"] == expect_choice, "wanted %r, got %r" % (expect_choice, dept["choice"])
    return dept, noul


def check_trace(state_dir):
    """Phase 1 task 12: newest trace in `state_dir` is a 200 with at least 8 observations."""
    db_path = os.path.join(state_dir, "state.sqlite3")
    assert os.path.exists(db_path), "state database %s not found" % db_path
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, status FROM traces ORDER BY ts_start DESC LIMIT 1"
        ).fetchone()
        assert row is not None, "no trace row in %s" % db_path
        trace_id, status = row
        assert status == 200, "newest trace %s has status %s, want 200" % (trace_id, status)
        count = conn.execute(
            "SELECT count(*) FROM observations WHERE trace_id = ?", (trace_id,)
        ).fetchone()[0]
        assert count >= 8, "trace %s has %d observations, want >= 8" % (trace_id, count)
        names = [r[0] for r in conn.execute(
            "SELECT name FROM observations WHERE trace_id = ? ORDER BY start_ms", (trace_id,))]
        print("trace_id=%s" % trace_id)
        print("observations: %s" % ", ".join(names))
    finally:
        conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Smoke test: proves a local CPU deployment of laya actually works.")
    parser.add_argument("--router", action="store_true",
                        help="Router path instead of direct single-checkpoint load")
    parser.add_argument("--skip-http", action="store_true",
                        help="engine checks only: skip the HTTP trace phase")
    parser.add_argument("--state-dir", metavar="DIR", default=None,
                        help="state dir for the HTTP phase (default: a fresh temp dir)")
    parser.add_argument("--http-port", type=int, metavar="PORT", default=8050,
                        help="port for the spawned layawatch server (default: 8050)")
    parser.add_argument("--keep-state", action="store_true",
                        help="keep the temp state dir instead of deleting it")
    return parser.parse_args(argv)


def _port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _log_tail(log_path, lines=20):
    try:
        with open(log_path, errors="replace") as fh:
            content = fh.read().splitlines()
    except OSError:
        return "(no log)"
    return "\n".join(content[-lines:]) or "(empty log)"


def _log_has(log_path, text):
    try:
        with open(log_path, errors="replace") as fh:
            return text in fh.read()
    except OSError:
        return False


def _wait_healthy(child, port, log_path):
    """Poll GET /healthz until 200, bounded by HEALTHZ_TIMEOUT_S; raise with the log tail."""
    url = "http://127.0.0.1:%d/healthz" % port
    deadline = time.perf_counter() + HEALTHZ_TIMEOUT_S
    while True:
        code = child.poll()
        if code is not None:
            raise RuntimeError("server exited with code %s before serving; log tail:\n%s"
                               % (code, _log_tail(log_path)))
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (OSError, http.client.HTTPException):
            pass  # connection refused / transient bad response: keep polling
        if time.perf_counter() >= deadline:
            raise RuntimeError("no 200 from %s within %.0fs; log tail:\n%s"
                               % (url, HEALTHZ_TIMEOUT_S, _log_tail(log_path)))
        time.sleep(0.01)


def _post_predict(port, state, questions):
    """POST /predict; return (status, parsed body) for both 2xx and error responses."""
    payload = json.dumps({"state": state, "questions": questions}).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:%d/predict" % port,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PREDICT_TIMEOUT_S) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body.decode("utf-8", "replace")


def _stop_server(child, log_path):
    """SIGTERM the child, wait for its 'layawatch stopped' line, escalate to SIGKILL."""
    if child.poll() is not None:
        return
    child.terminate()
    deadline = time.perf_counter() + STOP_TIMEOUT_S
    while time.perf_counter() < deadline:
        if _log_has(log_path, "layawatch stopped"):
            child.wait(timeout=5)
            return
        if child.poll() is not None:
            return
        time.sleep(0.05)
    child.kill()
    child.wait(timeout=5)


def run_http_phase(args):
    """Spawn a fresh layawatch server, POST /predict, then assert its persisted trace."""
    created = args.state_dir is None
    state_dir = tempfile.mkdtemp(prefix="lw-smoke-") if created else os.path.abspath(args.state_dir)
    os.makedirs(state_dir, exist_ok=True)
    child = None
    log_path = os.path.join(state_dir, "server.log")
    try:
        assert not _port_open(args.http_port), (
            "port %d already in use; pass --http-port" % args.http_port)
        env = os.environ.copy()  # every LAYA_* variable passes through untouched
        env["LAYA_STATE_DIR"] = state_dir
        env["LAYA_PORT"] = str(args.http_port)
        env.setdefault("HF_HOME", os.path.join(REPO_ROOT, ".hf-cache"))
        with open(log_path, "wb") as log_file:
            child = subprocess.Popen(
                [sys.executable, "-m", "layawatch"],
                cwd=REPO_ROOT,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        t0 = time.perf_counter()
        _wait_healthy(child, args.http_port, log_path)
        print("[http] server ready in %.0f ms (port=%d state=%s)"
              % ((time.perf_counter() - t0) * 1000, args.http_port, state_dir))

        t0 = time.perf_counter()
        status, body = _post_predict(args.http_port, STATE, QUESTIONS)
        ms = (time.perf_counter() - t0) * 1000
        assert status == 200, "POST /predict -> %d: %r" % (status, body)
        answers = body.get("answers") if isinstance(body, dict) else None
        assert answers is not None, "POST /predict 200 response has no answers: %r" % (body,)
        print("[http] POST /predict OK in %.0f ms (%d answers)" % (ms, len(answers)))

        deadline = time.perf_counter() + TRACE_WAIT_S  # wait out the writer batch
        while True:
            try:
                check_trace(state_dir)
                break
            except AssertionError:
                if time.perf_counter() >= deadline:
                    raise
                time.sleep(0.1)
    finally:
        if child is not None:
            _stop_server(child, log_path)
        if created and not args.keep_state:
            shutil.rmtree(state_dir, ignore_errors=True)
        elif created:
            print("[http] state kept at %s" % state_dir)


def main(argv=None):
    args = _parse_args(argv)

    if args.router:
        from laya import Router
        t0 = time.perf_counter()
        router = Router(preload=["english", "multilingual"])
        load_s = time.perf_counter() - t0
        print("[1/3] preloaded english+multilingual in %.1fs" % load_s)

        t0 = time.perf_counter()
        res = router.predict(STATE, QUESTIONS)
        en_ms = (time.perf_counter() - t0) * 1000
        dept, noul = check(res, expect_choice="billing")
        assert res["routing"]["model"] == "english", res["routing"]
        print("[2/3] english routed OK in %.0f ms: dept=%s conf=%.2f churn=%.2f"
              % (en_ms, dept["choice"], dept["confidence"], noul))

        t0 = time.perf_counter()
        res_hi = router.predict({"body": "मुझसे दो बार शुल्क लिया गया, कृपया पैसे वापस करें।"}, QUESTIONS)
        hi_ms = (time.perf_counter() - t0) * 1000
        check(res_hi)
        assert res_hi["routing"]["model"] == "multilingual", res_hi["routing"]
        print("[3/3] hindi routed OK in %.0f ms -> %s (%s)"
              % (hi_ms, res_hi["routing"]["model"], res_hi["routing"]["reason"][:60]))
        print("PASS: router path healthy on this machine (cpu fp32, %.0f ms en / %.0f ms hi)" % (en_ms, hi_ms))
    else:
        import laya
        t0 = time.perf_counter()
        agent = laya.load("convaiinnovations/laya")
        load_s = time.perf_counter() - t0
        print("[1/3] english checkpoint loaded in %.1fs (device=%s)" % (load_s, agent.device))
        assert agent.device.type == "cpu", "this box has no GPU; expected cpu, got %s" % agent.device

        t0 = time.perf_counter()
        res = agent.predict(STATE, QUESTIONS)
        ms = (time.perf_counter() - t0) * 1000
        dept, noul = check(res, expect_choice="billing")
        print("[2/3] predict OK in %.0f ms: dept=%s conf=%.2f churn=%.2f"
              % (ms, dept["choice"], dept["confidence"], noul))

        t0 = time.perf_counter()
        agent.predict(STATE, QUESTIONS)
        ms2 = (time.perf_counter() - t0) * 1000
        print("[3/3] second predict OK in %.0f ms (warm)" % ms2)
        print("PASS: single-checkpoint CPU hosting works (load %.1fs, warm %.0f ms)" % (load_s, ms2))

    if args.skip_http:
        print("[skip] HTTP trace phase skipped (--skip-http): no server, no trace assertions")
        return

    run_http_phase(args)


if __name__ == "__main__":
    main()
