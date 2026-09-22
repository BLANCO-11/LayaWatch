"""Local HTTP host for the laya decision engine, with a built-in admin page.

  GET    /                    service info
  GET    /admin               admin UI: usage, logs, API keys, model management
  GET    /healthz             liveness + preloaded checkpoints
  POST   /predict             router.predict(state, questions, model=?/task=?/lang=?)
  POST   /route               router.route(...) without a forward pass

  Admin JSON API (same origin):
  GET    /admin/api/stats     usage counters, latency, RSS, auth state
  GET    /admin/api/logs      recent log lines (tail)
  GET    /admin/api/keys      list keys (secret never re-exposed)
  POST   /admin/api/keys      {"name": ...} -> create; secret returned once
  DELETE /admin/api/keys      {"id": ...}   -> revoke
  GET    /admin/api/auth      {"enabled": bool}
  POST   /admin/api/auth      {"enabled": bool}  require keys on /predict and /route
  GET    /admin/api/models    loaded checkpoints
  POST   /admin/api/models    {"action": "load"|"unload", "models": [...]}

Auth: /predict and /route require `X-API-Key: <key>` (or `Authorization: Bearer <key>`)
only while the persisted toggle is on; creating the first key arms it automatically.
If LAYA_ADMIN_TOKEN is set, /admin/api/* requires `X-Admin-Token` (the admin page
prompts once and remembers it).

Env: LAYA_MODELS (checkpoints to preload; "all" for every one), LAYA_DEVICE,
LAYA_PORT (8050), LAYA_ADMIN_TOKEN, LAYA_STATE_DIR (default: this file's directory),
LAYA_ENGLISH_ONLY (1 = preload english only, 422 any non-English state or
multilingual override, never load the multilingual checkpoint)
"""
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from laya import Router
from laya.lang import analyse
from laya.router import normalise_name

BASE = Path(os.environ.get("LAYA_STATE_DIR") or Path(__file__).resolve().parent)
STATE_FILE = BASE / "api_keys.json"
ADMIN_PAGE = BASE / "admin.html"
MAX_BODY = 4 * 1024 * 1024  # 4 MiB request cap

MODELS = [m.strip() for m in os.environ.get("LAYA_MODELS", "english,multilingual").split(",") if m.strip()]
DEVICE = os.environ.get("LAYA_DEVICE") or None
PORT = int(os.environ.get("LAYA_PORT", "8050"))
# English-only deployment: preload only the English checkpoint, refuse states that do not
# detect as English (422) instead of routing them to multilingual, and remove multilingual
# from the router entirely so no code path can load it (~1.3 GB RAM saved).
ENGLISH_ONLY = os.environ.get("LAYA_ENGLISH_ONLY", "").strip().lower() in ("1", "true", "yes", "on")
if ENGLISH_ONLY:
    MODELS = ["english"]

START = time.time()
LOGS = deque(maxlen=500)
_lock = threading.Lock()          # guards STATE, STATS, LOGS
_predict_lock = threading.Lock()  # CPU inference: one forward pass at a time (BLAS oversubscription)

# ------------------------------------------------------------------ state
def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        if isinstance(data.get("keys"), list) and isinstance(data.get("enabled"), bool):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"enabled": False, "keys": []}


def _save_state() -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(_state, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


_state = _load_state()


def _log(msg: str) -> None:
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _lock:
        LOGS.append(line)
    sys.stderr.write(line + "\n")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _record(route: str, model: str | None, ms: float, status: int) -> None:
    with _lock:
        r = STATS.setdefault(route, {"count": 0, "errors": 0, "ms_total": 0.0,
                                     "ms_max": 0.0, "last_ms": 0.0, "last_at": None})
        r["count"] += 1
        r["errors"] += status >= 400
        r["ms_total"] += ms
        r["ms_max"] = max(r["ms_max"], ms)
        r["last_ms"] = ms
        r["last_at"] = _now()
        if model:
            m = MODEL_STATS.setdefault(model, {"count": 0, "errors": 0, "ms_total": 0.0,
                                               "ms_max": 0.0, "last_ms": 0.0, "last_at": None})
            m["count"] += 1
            m["errors"] += status >= 400
            m["ms_total"] += ms
            m["ms_max"] = max(m["ms_max"], ms)
            m["last_ms"] = ms
            m["last_at"] = _now()


STATS: dict = {}
MODEL_STATS: dict = {}


def _api_key_ok(headers) -> bool:
    with _lock:
        if not _state["enabled"]:
            return True
    provided = headers.get("X-API-Key") or ""
    auth = headers.get("Authorization") or ""
    if not provided and auth.lower().startswith("bearer "):
        provided = auth[7:]
    if not provided:
        return False
    digest = hashlib.sha256(provided.encode()).hexdigest()
    with _lock:
        for k in _state["keys"]:
            if hmac.compare_digest(k["sha256"], digest):
                k["last_used"] = _now()
                _save_state()
                return True
    return False


def _admin_ok(headers) -> bool:
    token = os.environ.get("LAYA_ADMIN_TOKEN")
    if not token:
        return True
    return hmac.compare_digest(headers.get("X-Admin-Token") or "", token)


def _rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return 0.0


# ------------------------------------------------------------------ server
router = Router(device=DEVICE, preload=(MODELS == ["all"]))
if MODELS != ["all"]:
    router.preload(MODELS)
if ENGLISH_ONLY:
    router.models.pop("multilingual", None)  # belt: explicit model= also cannot load it


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # access log into the ring buffer + stderr
        _log("%s - %s" % (self.address_string(), fmt % args))

    def _send(self, code: int, payload: dict | list, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def _send_html(self, code: int, text: str) -> None:
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def do_HEAD(self):  # health probes and proxies commonly HEAD the endpoint
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def _discard_body(self) -> None:
        """Consume an unread request body so a keep-alive connection stays framed.

        Early returns before _body() would otherwise leave the body in rfile; the
        client's next pipelined request then gets parsed against leftover bytes
        (observed in browsers: the JSON body was read as a request line and the
        following DELETE was answered 501). Too-large claims are not drained --
        the connection closes instead.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return
        if length > MAX_BODY:
            self.close_connection = True
            return
        while length > 0:
            chunk = self.rfile.read(min(length, 65536))
            if not chunk:
                self.close_connection = True
                return
            length -= len(chunk)

    def _body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            if length > MAX_BODY:
                self.close_connection = True  # too big to drain; drop the connection
            self._send(400, {"error": "missing or oversized body (max %d bytes)" % MAX_BODY})
            return None
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("body must be a JSON object")
            return data
        except (json.JSONDecodeError, ValueError) as e:
            self._send(400, {"error": "invalid JSON: %s" % e})
            return None

    # ------------------------------------------------------------- GET
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, {"service": "laya", "admin": "/admin", "predict": "/predict"})
        elif path == "/admin":
            try:
                self._send_html(200, ADMIN_PAGE.read_text())
            except OSError:
                self._send_html(500, "admin.html missing next to serve.py")
        elif path == "/healthz":
            self._send(200, {"status": "ok", "models": router.loaded,
                             "device": str(router.device or "auto"), "auth_enabled": _state["enabled"]})
        elif path.startswith("/admin/api/"):
            self._admin_get(path)
        else:
            self._send(404, {"error": "unknown path; use /healthz, /predict, /route, /admin"})

    def _admin_get(self, path: str) -> None:
        if not _admin_ok(self.headers):
            self._send(401, {"error": "missing or invalid X-Admin-Token"})
            return
        if path == "/admin/api/stats":
            with _lock:
                enabled = _state["enabled"]

                def pub(d):
                    return {**{k: d[k] for k in ("count", "errors", "last_at")},
                            "avg_ms": round(d["ms_total"] / d["count"], 1) if d["count"] else 0.0,
                            "max_ms": round(d["ms_max"], 1),
                            "last_ms": round(d["last_ms"], 1)}
                routes = {k: pub(v) for k, v in STATS.items()}
                models = {k: pub(v) for k, v in MODEL_STATS.items()}
                total = sum(v["count"] for v in STATS.values())
            self._send(200, {"uptime_s": int(time.time() - START), "rss_mb": _rss_mb(),
                             "auth_enabled": enabled, "total_requests": total,
                             "routes": routes, "models": models})
        elif path == "/admin/api/logs":
            with _lock:
                lines = list(LOGS)
            self._send(200, {"lines": lines[-300:]})
        elif path == "/admin/api/keys":
            with _lock:
                keys = [{"id": k["id"], "name": k["name"], "prefix": k["prefix"],
                         "created": k["created"], "last_used": k["last_used"]}
                        for k in _state["keys"]]
                enabled = _state["enabled"]
            self._send(200, {"keys": keys, "enabled": enabled})
        elif path == "/admin/api/auth":
            with _lock:
                enabled = _state["enabled"]
            self._send(200, {"enabled": enabled})
        elif path == "/admin/api/models":
            self._send(200, {"loaded": router.loaded, "default": MODELS})
        else:
            self._send(404, {"error": "unknown admin endpoint"})

    # ------------------------------------------------------------- POST
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path in ("/predict", "/route"):
            self._decide(path)
        elif path.startswith("/admin/api/"):
            self._admin_post(path)
        else:
            self._discard_body()
            self._send(404, {"error": "unknown path; use /healthz, /predict, /route, /admin"})

    def _decide(self, path: str) -> None:
        if not _api_key_ok(self.headers):
            self._discard_body()  # body was never read; keep the connection framed
            _record(path, None, 0.0, 401)
            self._send(401, {"error": "missing or invalid API key"}, {"WWW-Authenticate": "Bearer"})
            return
        payload = self._body()
        if payload is None:
            return
        if not isinstance(payload.get("state"), (str, dict, list)):
            self._send(400, {"error": "'state' must be a string, object, or array"})
            return
        questions = payload.get("questions")
        if not isinstance(questions, dict) or not questions:
            self._send(400, {"error": "'questions' must be a non-empty object"})
            return
        overrides = {k: payload[k] for k in ("model", "task", "lang") if payload.get(k)}
        if ENGLISH_ONLY:
            det = analyse(payload["state"])
            if not det.get("is_english"):
                _record(path, None, 0.0, 422)
                self._send(422, {
                    "error": "english-only deployment: state did not detect as English",
                    "detection": {k: det.get(k) for k in
                                  ("script", "language", "non_latin_fraction", "diacritic_rate")},
                })
                return
            try:
                wants_multi = bool(overrides.get("model")) and \
                    normalise_name(overrides["model"]) == "multilingual"
                lang_o = overrides.get("lang")
                if lang_o and str(lang_o).lower().split("-")[0] not in ("en", "eng", "english"):
                    wants_multi = True
            except ValueError:
                wants_multi = False  # unknown model name: let predict() answer 400 with its message
            if wants_multi:
                _record(path, None, 0.0, 422)
                self._send(422, {"error": "english-only deployment: multilingual checkpoint not available"})
                return
        t0 = time.perf_counter()
        model_used = overrides.get("model")
        try:
            if path == "/route":
                result = dict(router.route(payload["state"], questions, **overrides))
                self._send(200, result)
                model_used = result.get("model")
            else:
                with _predict_lock:
                    result = router.predict(payload["state"], questions, **overrides)
                model_used = (result.get("routing") or {}).get("model") or model_used
                self._send(200, result)
        except (ValueError, KeyError) as e:  # bad question schema / unknown model / qtype
            _record(path, model_used, (time.perf_counter() - t0) * 1000, 400)
            self._send(400, {"error": str(e)})
            return
        except Exception as e:  # never leave the client hanging on an inference crash
            _record(path, model_used, (time.perf_counter() - t0) * 1000, 500)
            _log("inference error: %r" % (e,))
            self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
            return
        _record(path, model_used, (time.perf_counter() - t0) * 1000, 200)

    def _admin_post(self, path: str) -> None:
        if not _admin_ok(self.headers):
            self._discard_body()
            self._send(401, {"error": "missing or invalid X-Admin-Token"})
            return
        payload = self._body()
        if payload is None:
            return
        if path == "/admin/api/keys":
            name = str(payload.get("name") or "").strip()
            if not name:
                self._send(400, {"error": "'name' is required"})
                return
            secret = "laya_" + secrets.token_hex(24)
            entry = {"id": secrets.token_hex(6), "name": name, "prefix": secret[:12],
                     "sha256": hashlib.sha256(secret.encode()).hexdigest(), "created": _now(),
                     "last_used": None}
            with _lock:
                _state["keys"].append(entry)
                first = not _state["enabled"]
                _state["enabled"] = True  # creating a key arms enforcement
                _save_state()
            _log("api key created: %s (%s...)%s" % (name, entry["prefix"],
                                                    "; auth armed" if first else ""))
            self._send(200, {"id": entry["id"], "name": name, "key": secret,
                             "prefix": entry["prefix"], "enabled": True,
                             "note": "store this key now; it is not shown again"})
        elif path == "/admin/api/auth":
            if not isinstance(payload.get("enabled"), bool):
                self._send(400, {"error": "'enabled' must be a boolean"})
                return
            with _lock:
                _state["enabled"] = payload["enabled"]
                _save_state()
            _log("api key enforcement %s" % ("ENABLED" if payload["enabled"] else "disabled"))
            self._send(200, {"enabled": payload["enabled"]})
        elif path == "/admin/api/models":
            action = payload.get("action")
            names = payload.get("models")
            if action not in ("load", "unload") or not isinstance(names, list) or not names:
                self._send(400, {"error": "'action' must be load|unload and 'models' a non-empty list"})
                return
            try:
                if action == "load":
                    router.preload(names)   # raises on unknown names; raises max_loaded to fit
                    _log("models loaded: %s" % ",".join(names))
                else:
                    for n in names:
                        router.unload(n)
                    _log("models unloaded: %s" % ",".join(names))
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            self._send(200, {"loaded": router.loaded})
        else:
            self._send(404, {"error": "unknown admin endpoint"})

    # ------------------------------------------------------------- DELETE
    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        if path != "/admin/api/keys":
            self._discard_body()
            self._send(404, {"error": "unknown path"})
            return
        if not _admin_ok(self.headers):
            self._discard_body()
            self._send(401, {"error": "missing or invalid X-Admin-Token"})
            return
        payload = self._body()
        if payload is None:
            return
        key_id = str(payload.get("id") or "")
        with _lock:
            before = len(_state["keys"])
            _state["keys"] = [k for k in _state["keys"] if k["id"] != key_id]
            removed = before - len(_state["keys"])
            _save_state()
        if not removed:
            self._send(404, {"error": "no key with id %r" % key_id})
            return
        _log("api key revoked: id=%s (enforcement stays %s)"
             % (key_id, "ON" if _state["enabled"] else "off"))
        self._send(200, {"revoked": key_id, "enabled": _state["enabled"]})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    _log("laya serving on http://127.0.0.1:%d (preloaded: %s, device: %s, auth: %s)"
         % (PORT, ",".join(router.loaded), router.device or "auto",
            "on" if _state["enabled"] else "off"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
