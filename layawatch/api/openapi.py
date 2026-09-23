"""OpenAPI 3.1 document for the LayaWatch HTTP surface.

The FastAPI app in :mod:`layawatch.app` is a single catch-all route that delegates to the
custom router, so FastAPI's own schema describes ``/{path}`` and nothing else. This module
is the real document: one entry per operation the router registers, served at
``/api/openapi.json`` and rendered by Swagger UI at ``/api/docs``.

Two rules keep it honest:

* **The code is the source.** Field names, gates, audit actions and error codes below are
  copied from the handlers, not from ``docs/api-reference.md``. Where the two disagree the
  handler wins and the description says what the handler actually does.
* **Drift is visible.** :func:`undocumented` compares this table against the live router
  table; :func:`layawatch.app.create_app` logs any registered route the table misses.

Security is per operation and reflects what the handlers enforce today. ``gate()``
(``layawatch/auth/sessions.py``) accepts a session cookie or an API key and applies the
permission table; endpoints that never call it pass through unauthenticated (the browser
session check in ``http/middleware.py`` only validates a cookie that is *present*, and the
default bind is loopback). Those operations say so instead of implying a credential is
required.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import layawatch

TITLE = "LayaWatch API"
DESCRIPTION = """\
Self-hosted observability console and decision-engine host: traces, metrics, logs, live
SSE, checkpoints, API keys, users and the playground.

**Credentials.** Console calls use the `lw_session` cookie (HttpOnly, SameSite=Lax) plus
the `X-CSRF-Token` header echoing the `lw_csrf` cookie on every mutation. Engine and CI
callers use `X-API-Key: lay_...` or `Authorization: Bearer lay_...`. Engine endpoints
(`POST /predict`, `POST /route`) verify keys only while key auth is armed.

**Conventions.** Errors are `{"error": {"code", "message", "details?"}}` with stable
snake_case codes. Lists are cursor paginated (`limit` max 200, default 50) and answer
`{"items", "next_cursor", "total_estimate"}`. Unknown query parameters are a `400
invalid_filter`, never ignored. Time windows take `since`/`until` epoch seconds or the
`range` shorthand (`15m`, `1h`, `6h`, `24h`, `7d`). Every response carries `X-Request-Id`.

**Enforcement note.** Operations marked "no credential is enforced" pass through the
browser-session check when no `lw_session` cookie is presented, exactly as the handlers
behave today; the deployment default binds loopback. A cookie that *is* presented is
always validated (401 dead session, 403 password change required, 403 missing CSRF echo).
"""

TAGS: tuple[tuple[str, str], ...] = (
    ("Service", "Liveness, service info, runtime metadata and the live event stream."),
    ("Engine", "The laya-compatible inference surface: predict and route."),
    ("Traces", "Recorded requests with their spans, scores, tags and deletion."),
    ("Metrics", "Rollup series, the KPI summary and model mix buckets."),
    ("Logs", "The log ring and its JSONL export."),
    ("Audit", "The append-only audit trail."),
    ("Settings", "Runtime controls and the rate-limit policy surface."),
    ("Auth", "Sign-in, sign-out, session identity and the first-run wizard."),
    ("Users", "User administration and active sessions."),
    ("API keys", "Engine credentials and the key-auth switch."),
    ("Models", "Checkpoint inventory, loading and unloading."),
    ("Playground", "Built-in templates and one-off in-process runs."),
)

SECURITY_SCHEMES: dict[str, Any] = {
    "sessionCookie": {
        "type": "apiKey",
        "in": "cookie",
        "name": "lw_session",
        "description": (
            "Console session cookie (HttpOnly, SameSite=Lax, Secure behind TLS). Set by "
            "`POST /api/v1/auth/login` and by the first-run setup."
        ),
    },
    "csrfToken": {
        "type": "apiKey",
        "in": "header",
        "name": "X-CSRF-Token",
        "description": (
            "Double-submit token: must echo the `lw_csrf` cookie on every mutating request "
            "made with a session cookie. API-key callers do not send it."
        ),
    },
    "apiKey": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": (
            "Engine credential, `lay_` plus 32 base62 characters. `Authorization: Bearer "
            "lay_...` is accepted as an alternative."
        ),
    },
}

#: Session cookie or API key; the gate applies the permission table.
SEC_READ: list[dict[str, list[str]]] = [{"sessionCookie": []}, {"apiKey": []}]
#: Same, and a session caller must echo the CSRF cookie.
SEC_WRITE: list[dict[str, list[str]]] = [{"sessionCookie": [], "csrfToken": []}, {"apiKey": []}]
#: Engine endpoints: keys only, and only while key auth is armed.
SEC_ENGINE: list[dict[str, list[str]]] = [{"apiKey": []}]


def _obj(props: Mapping[str, Any], required: Sequence[str] = ()) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": dict(props)}
    if required:
        schema["required"] = list(required)
    return schema


def _arr(items: Any) -> dict:
    return {"type": "array", "items": items}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _str(description: str = "") -> dict:
    return {"type": "string", "description": description} if description else {"type": "string"}


def _int(description: str = "") -> dict:
    return {"type": "integer", "description": description} if description else {"type": "integer"}


def _num(description: str = "") -> dict:
    return {"type": "number", "description": description} if description else {"type": "number"}


def _bool(description: str = "") -> dict:
    return {"type": "boolean", "description": description} if description else {"type": "boolean"}


def _nullable(schema: dict) -> dict:
    return {**schema, "nullable": True}


#: Time-window shorthand shared by every read endpoint (store/queries.RANGE_SECONDS).
_RANGE = {"type": "string", "enum": ["15m", "1h", "6h", "24h", "7d"]}

_EVENT_ITEM = _obj(
    {
        "subject": _str("Rate-limit subject: key id, ip, email or session id."),
        "scope": _str("Bucket scope."),
        "used": _num("Tokens taken since the last full refill."),
        "limit": _int("Bucket capacity for the window."),
        "resets_in": _int("Seconds until the bucket is full again."),
    }
)

SCHEMAS: dict[str, Any] = {
    "ErrorEnvelope": _obj(
        {
            "error": _obj(
                {
                    "code": _str("Stable snake_case error code."),
                    "message": _str("Human-readable message; never a stack trace."),
                    "details": _nullable(_obj({}, ())),
                },
                ("code", "message"),
            )
        },
        ("error",),
    ),
    "TraceSummary": _obj(
        {
            "id": _str("Trace id; equals `X-Request-Id` for engine calls."),
            "ts_start": _num("Epoch seconds."),
            "duration_ms": _num("Total handled duration."),
            "route": _str("Request path."),
            "method": _str("HTTP method."),
            "status": _int("Response status."),
            "model": _nullable(_str("Checkpoint that answered.")),
            "route_reason": _nullable(_str("Why the router picked that checkpoint.")),
            "queue_ms": _nullable(_num("Time waiting for the inference slot.")),
            "forward_ms": _nullable(_num("Forward-pass time.")),
            "client_key_id": _nullable(_str("Presented API key id, when any.")),
            "error_code": _nullable(_str("Error code when the request failed.")),
            "span_count": _int("Observation count."),
        },
        ("id", "ts_start", "duration_ms", "route", "method", "status"),
    ),
    "Observation": _obj(
        {
            "id": _str("Observation id."),
            "trace_id": _str("Owning trace."),
            "parent_id": _nullable(_str("Parent span id.")),
            "type": _str("`span` or `generation` in this endpoint."),
            "name": _str("Span name, e.g. `forward`."),
            "started_at": _num("Epoch seconds."),
            "ended_at": _nullable(_num("Epoch seconds.")),
            "duration_ms": _nullable(_num("Span duration.")),
            "status": _str("`ok` or `error`."),
            "attributes": _nullable(_obj({}, ())),
            "events": _nullable(_arr(_obj({}, ()))),
        },
        ("id", "trace_id", "type", "name"),
    ),
    "LogEntry": _obj(
        {
            "id": _int("Ring id."),
            "ts": _num("Epoch seconds."),
            "level": _str("Log level."),
            "source": _str("Emitting component."),
            "trace_id": _nullable(_str("Trace this line belongs to.")),
            "message": _str("Log line."),
        },
        ("id", "ts", "level", "source", "message"),
    ),
    "AuditEntry": _obj(
        {
            "id": _int("Row id."),
            "ts": _num("Epoch seconds."),
            "actor_id": _nullable(_str("Acting user id.")),
            "actor": _str("Acting principal (email or `api`)."),
            "action": _str("Audit action, e.g. `key.created`."),
            "target": _nullable(_str("Affected object.")),
            "result": _str("`ok` or `denied`."),
            "meta": _nullable(_obj({}, ())),
        },
        ("id", "ts", "actor", "action", "result"),
    ),
    "KeyRecord": _obj(
        {
            "id": _str("Key id."),
            "name": _str("Operator label."),
            "prefix": _str("First 8 characters of the secret."),
            "created_at": _num("Epoch seconds."),
            "last_used": _nullable(_num("Epoch seconds.")),
            "revoked_at": _nullable(_num("Epoch seconds; soft delete.")),
            "request_count": _int("Requests presented with this key."),
            "rate_limit_per_min": _nullable(_int("Per-key override; null uses the policy.")),
            "burst": _nullable(_int("Per-key burst override.")),
        },
        ("id", "name", "prefix", "created_at"),
    ),
    "User": _obj(
        {
            "id": _str("User id."),
            "email": _str("Normalized email."),
            "name": _str("Display name."),
            "role": _str("`owner`, `admin` or `viewer`."),
            "created_at": _num("Epoch seconds."),
            "last_login_at": _nullable(_num("Epoch seconds.")),
            "disabled": _bool("Sign-in refused while true."),
            "must_change": _bool("Every action refused until the password changes."),
        },
        ("id", "email", "name", "role"),
    ),
    "Session": _obj(
        {
            "id": _str("Session id."),
            "created_at": _num("Epoch seconds."),
            "expires_at": _num("Epoch seconds."),
            "last_seen": _nullable(_num("Epoch seconds.")),
            "user_agent": _nullable(_str("Client user agent.")),
            "ip": _nullable(_str("Client ip.")),
        },
        ("id", "created_at", "expires_at"),
    ),
    "RatelimitPolicy": _obj(
        {
            "enabled": _bool("Master switch; read per request by the login backoff."),
            "engine_per_min": _int("Engine requests per minute per key. >= 0."),
            "ip_per_min": _int("Requests per minute per ip. >= 0."),
            "mutation_per_min": _int("Mutations per minute per session. >= 0."),
            "playground_per_min": _int("Playground runs per minute. >= 0."),
            "login": _int("Failed logins allowed per window. >= 1."),
            "login_window": {"type": "integer", "enum": [300, 900, 3600]},
            "engine_max_inflight": _int("Concurrent forward passes. >= 1."),
            "engine_queue_max": _int("Queued engine requests. >= 1."),
            "loopback_exempt": _bool("Skip ip limits for loopback callers."),
            "keys": _arr(
                _obj(
                    {
                        "id": _str("Key id."),
                        "prefix": _str("Secret prefix."),
                        "rate_limit_per_min": _nullable(_int()),
                        "burst": _nullable(_int()),
                    }
                )
            ),
        }
    ),
    "SettingsPayload": _obj(
        {
            "config": _obj({}, ()),
            "settings": _obj({}, ()),
            "effective": _obj(
                {
                    "retention_traces": _int(),
                    "retention_days": _int(),
                    "log_ring_size": _int(),
                    "trace_sample": _num(),
                    "stream_tick": _int(),
                    "capture_payloads": _bool(),
                }
            ),
            "note": _str("Read-time precedence note."),
        },
        ("config", "settings", "effective"),
    ),
    "PredictRequest": _obj(
        {
            "state": _obj({}, ()),
            "questions": _obj({}, ()),
            "model": _str("Checkpoint override."),
            "task": _str("Engine task name."),
            "lang": _str("Language hint recorded on the span."),
        },
        ("state", "questions"),
    ),
    "PredictResponse": _obj(
        {
            "answers": _obj({}, ()),
            "model": _str("Checkpoint that answered."),
            "route_reason": _str("Why that checkpoint was chosen."),
            "lang": _nullable(_str("Detected language.")),
        },
        ("answers", "model"),
    ),
    "ModelStats": _obj(
        {
            "size_bytes": _nullable(_int("From the newest `model.load` span.")),
            "load_ms": _nullable(_num("Load duration.")),
            "requests_24h": _int("Step-60 rollup counter over 24 h; 0 until recorded."),
            "p50_ms": _nullable(_num("Count-weighted mean of bucket p50s.")),
        }
    ),
}


def _q(name: str, kind: str, description: str, *, required: bool = False,
       values: Sequence[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": kind}
    if values:
        schema["enum"] = list(values)
    return {
        "name": name,
        "in": "query",
        "required": required,
        "description": description,
        "schema": schema,
    }


def _page(items: Any, description: str = "Keyset page, newest first.") -> dict:
    return _obj(
        {
            "items": items,
            "next_cursor": _nullable(_str("Opaque cursor; null on the last page.")),
            "total_estimate": _int("Filtered row count."),
        },
        ("items", "next_cursor", "total_estimate"),
    )


def _op(
    tag: str,
    operation_id: str,
    summary: str,
    description: str,
    *,
    query: Iterable[dict] = (),
    body: tuple[dict, bool, str] | None = None,
    ok: tuple[int, str, dict | None] | None = None,
    errors: Iterable[tuple[int, str]] = (),
    security: list[dict[str, list[str]]] | None = None,
) -> dict:
    """Build one operation object; ``ok`` is ``(status, description, schema|None)``."""
    responses: dict[str, Any] = {}
    if ok is not None:
        status, note, schema = ok
        entry: dict[str, Any] = {"description": note}
        if schema is not None:
            entry["content"] = {"application/json": {"schema": schema}}
        responses[str(status)] = entry
    for status, note in errors:
        responses.setdefault(
            str(status),
            {
                "description": note,
                "content": {"application/json": {"schema": _ref("ErrorEnvelope")}},
            },
        )
    operation: dict[str, Any] = {
        "tags": [tag],
        "summary": summary,
        "description": description,
        "operationId": operation_id,
        "responses": responses,
    }
    params = list(query)
    if params:
        operation["parameters"] = params
    if body is not None:
        schema, required, note = body
        operation["requestBody"] = {
            "required": required,
            "description": note,
            "content": {"application/json": {"schema": schema}},
        }
    if security is not None:
        operation["security"] = security
    return operation


#: The 4xx/5xx responses every gated endpoint can produce.
_GATE_ERRORS = (
    (401, "`missing_or_invalid_credential`: no session cookie and no verifiable API key."),
    (403, "`forbidden`: the role or permission table refused; or `password_change_required`."),
    (500, "`internal_error`: unexpected failure; detail is logged, never returned."),
)
_BAD_FILTER = (400, "`invalid_filter`: unknown or malformed query parameter.")


OPERATIONS: dict[tuple[str, str], dict] = {
    # ------------------------------------------------------------------ Service
    ("GET", "/"): _op(
        "Service",
        "serviceInfo",
        "Service info or the console",
        "Answers the JSON service descriptor when `Accept` explicitly includes "
        "`application/json` and not `text/html`; every other request serves the console's "
        "`index.html` (or the build-instructions page when the export is absent). No "
        "credential is enforced.",
        ok=(200, "Service descriptor, or the console HTML.", _obj(
            {
                "service": _str(),
                "version": _str(),
                "docs": _str("Project URL."),
                "ui": _str("Console path."),
            }
        )),
    ),
    ("GET", "/healthz"): _op(
        "Service",
        "healthz",
        "Liveness probe",
        "Container and systemd liveness probe. Answers without touching SQLite and never "
        "requires a credential; `device` is `fake` when the laya package is not importable.",
        ok=(200, "Liveness payload.", _obj(
            {
                "status": _str("Always `ok` while the process serves."),
                "models": _arr(_str()),
                "device": _str("`auto`, `cpu`, `cuda` or `fake`."),
                "auth_enabled": _bool("Whether engine key auth is armed."),
            },
            ("status",),
        )),
    ),
    ("GET", "/api/v1/meta"): _op(
        "Service",
        "meta",
        "Runtime metadata",
        "Version, uptime and the writer's self-observability counters. `config` is reduced "
        "to an allowlist of safe fields: no path and no secret is ever included. No "
        "credential is enforced (the console sends its session cookie).",
        ok=(200, "Metadata and counters.", _obj(
            {
                "version": _str(),
                "uptime_s": _num(),
                "started_at": _num("Epoch seconds."),
                "db_size_bytes": _int(),
                "rss_mb": _num(),
                "sse_clients": _int("Connected stream clients."),
                "config": _obj({}, ()),
            },
            ("version", "uptime_s", "started_at"),
        )),
    ),
    ("GET", "/api/v1/stream"): _op(
        "Service",
        "stream",
        "Live event stream (SSE)",
        "Server-sent events for the console: `hello` on connect, `pulse` every tick "
        "(default 3 s), `trace` as traces flush, `log` per line, `model` on checkpoint "
        "change and `ping` every 15 s to keep proxies open. Reconnect with the "
        "`Last-Event-ID` header or the `last_event_id` query parameter (an EventSource "
        "cannot set headers on reconnect; the header wins when both are present) to replay "
        "up to 200 missed trace events. No credential is enforced.",
        query=[
            _q(
                "last_event_id",
                "integer",
                "Replay trace events newer than this id; unparseable or absent means no replay.",
            )
        ],
        ok=(200, "`text/event-stream` of framed events.", None),
    ),
    # ------------------------------------------------------------------ Engine
    ("POST", "/predict"): _op(
        "Engine",
        "predict",
        "Run a prediction",
        "The laya-compatible inference call. The body must be a JSON object with object "
        "`state` and object `questions`; `model` and `task` are passed to the adapter "
        "verbatim and `lang` is recorded on the trace (a `temperature` field is ignored by "
        "this handler). Records one trace whose id is the response's `X-Request-Id`. Keys "
        "are verified only while key auth is armed.",
        body=(
            _ref("PredictRequest"),
            True,
            "`state` and `questions` are required objects; `model`, `task` and `lang` are optional.",
        ),
        ok=(200, "The adapter's answer payload.", _ref("PredictResponse")),
        errors=(
            (400, "`invalid_request`: missing `state`/`questions`, or the engine rejected the call."),
            (401, "`missing_or_invalid_credential`: key auth is armed and the key is absent or unknown."),
            (422, "`english_only`: non-English state in an english-only deployment; `details.detection`."),
            (500, "`internal_error`: unexpected engine failure."),
        ),
        security=SEC_ENGINE,
    ),
    ("POST", "/route"): _op(
        "Engine",
        "route",
        "Routing decision only",
        "Answers the routing decision without a forward pass: same body shape as `POST "
        "/predict`, same trace recording, no inference. Keys are verified only while key "
        "auth is armed.",
        body=(_ref("PredictRequest"), True, "Same shape as `POST /predict`."),
        ok=(200, "The adapter's routing decision.", _ref("PredictResponse")),
        errors=(
            (400, "`invalid_request`: missing `state`/`questions`, or the engine rejected the call."),
            (401, "`missing_or_invalid_credential`: key auth is armed and the key is absent or unknown."),
            (422, "`english_only`: non-English state in an english-only deployment."),
            (500, "`internal_error`: unexpected engine failure."),
        ),
        security=SEC_ENGINE,
    ),
    # ------------------------------------------------------------------ Traces
    ("GET", "/api/v1/traces"): _op(
        "Traces",
        "listTraces",
        "List traces",
        "Keyset-paginated trace list, newest first by default. `status` accepts an integer "
        "or a class (`4xx`); `q` matches the id or the error message; a cursor issued for a "
        "different `order` is rejected. No credential is enforced.",
        query=[
            _q("route", "string", "Exact request path, e.g. `/predict`."),
            _q("status", "string", "Integer status or a class such as `4xx`/`5xx`."),
            _q("model", "string", "Checkpoint name."),
            _q("q", "string", "Substring of the trace id or the error message."),
            _q("min_duration_ms", "number", "Lower bound on total duration."),
            _q("tag", "string", "Traces carrying this tag."),
            _q("since", "number", "Epoch seconds, inclusive."),
            _q("until", "number", "Epoch seconds, exclusive."),
            _q("range", "string", "Window shorthand.", values=["15m", "1h", "6h", "24h", "7d"]),
            _q("limit", "integer", "Page size, 1-200 (default 50)."),
            _q("cursor", "string", "Opaque cursor from the previous page."),
            _q("order", "string", "Sort order.", values=["newest", "slowest"]),
        ],
        ok=(200, "Trace summaries plus `span_count`.", _page(_ref("TraceSummary"))),
        errors=(_BAD_FILTER, (500, "`internal_error`.")),
    ),
    ("GET", "/api/v1/traces/{id}"): _op(
        "Traces",
        "getTrace",
        "Trace detail",
        "One trace with its observations, scores, related log lines and `meta`. No "
        "credential is enforced.",
        query=[_q("id", "string", "Trace id (path).", required=True)],
        ok=(200, "Trace detail.", _obj(
            {
                "trace": _ref("TraceSummary"),
                "observations": _arr(_ref("Observation")),
                "scores": _arr(_obj({}, ())),
                "logs": _arr(_ref("LogEntry")),
                "meta": _obj({}, ()),
            }
        )),
        errors=((404, "`not_found`: no such trace."), (500, "`internal_error`.")),
    ),
    ("GET", "/api/v1/traces/{id}/observations"): _op(
        "Traces",
        "listTraceObservations",
        "Trace observations",
        "Span-shaped observations only (`span` and `generation`); instantaneous `event` rows "
        "stay detail-only. Used to lazy-load the waterfall. No credential is enforced.",
        query=[_q("id", "string", "Trace id (path).", required=True)],
        ok=(200, "`{items: [...]}` of span-shaped observations.", _obj(
            {"items": _arr(_ref("Observation"))}, ("items",)
        )),
        errors=((404, "`not_found`: no such trace."), (500, "`internal_error`.")),
    ),
    ("POST", "/api/v1/traces/{id}/scores"): _op(
        "Traces",
        "createTraceScore",
        "Attach a score",
        "Appends one score to a trace and audits `trace.scored`. `name` and a scalar "
        "`value` are required; `data_type` is inferred from the value when omitted; "
        "`comment` is capped at 500 characters and `source` is fixed to `api`. No "
        "credential is enforced.",
        query=[_q("id", "string", "Trace id (path).", required=True)],
        body=(
            _obj(
                {
                    "name": _str("Score name."),
                    "value": _str("Scalar: bool, finite number or string."),
                    "data_type": _str("Stored kind."),
                    "comment": _str("At most 500 characters."),
                },
                ("name", "value"),
            ),
            True,
            "`name` and `value` are required; `data_type` and `comment` are optional.",
        ),
        ok=(201, "The stored score.", _obj(
            {
                "id": _str(),
                "name": _str(),
                "value": _nullable({}),
                "data_type": _str("`numeric`, `boolean` or `categorical`."),
                "source": _str("Always `api`."),
                "comment": _nullable(_str()),
                "ts": _num("Epoch seconds."),
            }
        )),
        errors=(
            (400, "`invalid_request`: bad field, non-scalar value, over-long comment."),
            (404, "`not_found`: no such trace."),
            (500, "`internal_error`."),
        ),
    ),
    ("POST", "/api/v1/traces/{id}/tags"): _op(
        "Traces",
        "tagTrace",
        "Add or remove tags",
        "Adds and removes free-form tags in one call and audits `trace.tagged`. Tags are "
        "free-form strings; a trace may carry at most 10. No credential is enforced.",
        query=[_q("id", "string", "Trace id (path).", required=True)],
        body=(
            _obj({"add": _arr(_str()), "remove": _arr(_str())}),
            True,
            "Both lists optional; `add` appends when absent, `remove` filters first.",
        ),
        ok=(200, "The resulting tag list.", _obj({"tags": _arr(_str())}, ("tags",))),
        errors=(
            (400, "`invalid_request`: more than 10 tags or a bad list."),
            (404, "`not_found`: no such trace."),
            (500, "`internal_error`."),
        ),
    ),
    ("DELETE", "/api/v1/traces/{id}"): _op(
        "Traces",
        "deleteTrace",
        "Delete one trace",
        "Deletes a trace; observations and scores cascade through their foreign keys, "
        "`log_entry` rows are kept (no foreign key) and metric rollups are untouched. "
        "Audits `trace.deleted`. No credential is enforced by this handler.",
        query=[_q("id", "string", "Trace id (path).", required=True)],
        ok=(204, "Deleted; no body.", None),
        errors=((404, "`not_found`: no such trace."), (500, "`internal_error`.")),
    ),
    ("DELETE", "/api/v1/traces"): _op(
        "Traces",
        "deleteTraceRange",
        "Bulk delete by time window",
        "Danger-zone range delete: at least one time bound is required (an unbounded delete "
        "is a 400, never an accident), `since` inclusive and `until` exclusive, `since < "
        "until` when both are given. Requires `traces.delete` (admin+), audits "
        "`trace.deleted` with the range and the deleted count in the same transaction, and "
        "leaves log rows and metric rollups in place.",
        query=[
            _q("since", "number", "Epoch seconds, inclusive."),
            _q("until", "number", "Epoch seconds, exclusive."),
            _q("range", "string", "Window shorthand; cannot be combined with since/until.",
               values=["15m", "1h", "6h", "24h", "7d"]),
        ],
        ok=(200, "Deleted row count and the window actually applied.", _obj(
            {"deleted": _int(), "since": _nullable(_num()), "until": _nullable(_num())},
            ("deleted",),
        )),
        errors=(
            _BAD_FILTER,
            (400, "`invalid_filter`: no time bound, `since >= until`, or range plus bounds."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    # ------------------------------------------------------------------ Metrics
    ("GET", "/api/v1/metrics"): _op(
        "Metrics",
        "metricsSeries",
        "Rollup series",
        "Bucket series for one or more metrics. Percentiles are bucket-level and the step "
        "is server-chosen unless it is one of the persisted rollup steps. No credential is "
        "enforced.",
        query=[
            _q("metrics", "string", "Comma-separated names; at least one of `requests`, "
               "`latency`, `errors`, `queue`.", required=True),
            _q("range", "string", "Window shorthand.", values=["15m", "1h", "6h", "24h", "7d"]),
            _q("since", "number", "Epoch seconds, inclusive."),
            _q("until", "number", "Epoch seconds, exclusive."),
            _q("step", "integer", "Rollup step.", values=["10", "60", "3600"]),
            _q("model", "string", "Restrict to one checkpoint."),
            _q("route", "string", "Restrict to one route."),
        ],
        ok=(200, "Series with their window and step.", _obj(
            {
                "step": _int(),
                "window": _obj({"since": _num(), "until": _num()}, ("since", "until")),
                "series": _arr(
                    _obj(
                        {
                            "metric": _str(),
                            "points": _arr(_arr({})),
                        },
                        ("metric", "points"),
                    )
                ),
                "note": _str("Percentiles are bucket-level."),
            },
            ("step", "window", "series"),
        )),
        errors=(_BAD_FILTER, (500, "`internal_error`.")),
    ),
    ("GET", "/api/v1/metrics/summary"): _op(
        "Metrics",
        "metricsSummary",
        "KPI strip",
        "KPI values plus deltas against the previous window of the same length. Defaults to "
        "the 15-minute window. No credential is enforced.",
        query=[_q("range", "string", "Window shorthand.", values=["15m", "1h", "6h", "24h", "7d"])],
        ok=(200, "KPI strip values and deltas.", _obj({}, ())),
        errors=(_BAD_FILTER, (500, "`internal_error`.")),
    ),
    ("GET", "/api/v1/metrics/models"): _op(
        "Metrics",
        "metricsModels",
        "Model mix buckets",
        "Model mix for the stacked bar chart. The window must fit the bucket count. No "
        "credential is enforced.",
        query=[
            _q("range", "string", "Window shorthand.", values=["15m", "1h", "6h", "24h", "7d"]),
            _q("since", "number", "Epoch seconds, inclusive."),
            _q("until", "number", "Epoch seconds, exclusive."),
        ],
        ok=(200, "Model mix buckets.", _obj({}, ())),
        errors=(
            _BAD_FILTER,
            (400, "`invalid_filter`: window too large for the bucket count."),
            (500, "`internal_error`."),
        ),
    ),
    # ------------------------------------------------------------------ Logs
    ("GET", "/api/v1/logs"): _op(
        "Logs",
        "listLogs",
        "Tail the log ring",
        "Newest-first log lines with the standard pagination envelope. Items carry exactly "
        "`id, ts, level, source, trace_id, message`. No credential is enforced.",
        query=[
            _q("level", "string", "Log level, e.g. `info`, `warning`, `error`."),
            _q("q", "string", "Substring match on the message."),
            _q("trace_id", "string", "Lines belonging to one trace."),
            _q("since", "number", "Epoch seconds, inclusive."),
            _q("until", "number", "Epoch seconds, exclusive."),
            _q("range", "string", "Window shorthand.", values=["15m", "1h", "6h", "24h", "7d"]),
            _q("limit", "integer", "Page size, 1-200 (default 50)."),
            _q("cursor", "string", "Opaque cursor from the previous page."),
        ],
        ok=(200, "Log page.", _page(_ref("LogEntry"))),
        errors=(_BAD_FILTER, (500, "`internal_error`.")),
    ),
    ("GET", "/api/v1/logs/export"): _op(
        "Logs",
        "exportLogs",
        "Export logs as JSONL",
        "Downloads up to 10 000 newest matching lines as a `text/plain` attachment with one "
        "JSON object per line. The window is resolved once so every internal page reads the "
        "same slice; an empty result is an empty 200 body. The documented owner/admin gate "
        "is not enforced by this handler yet.",
        query=[
            _q("range", "string", "Window shorthand; defaults to `1h`.",
               values=["15m", "1h", "6h", "24h", "7d"]),
            _q("format", "string", "Only `jsonl` is accepted.", required=True, values=["jsonl"]),
        ],
        ok=(200, "`text/plain` JSONL attachment.", None),
        errors=(_BAD_FILTER, (500, "`internal_error`.")),
    ),
    # ------------------------------------------------------------------ Audit
    ("GET", "/api/v1/audit"): _op(
        "Audit",
        "listAudit",
        "Audit trail",
        "Append-only audit entries, newest first, with the standard pagination envelope and "
        "keyset cursors. `action` is an exact match and `actor` a case-insensitive "
        "substring. `meta` is returned already parsed. Requires `audit.read` (admin+).",
        query=[
            _q("actor", "string", "Case-insensitive substring of the actor."),
            _q("action", "string", "Exact action name, e.g. `key.created`."),
            _q("since", "number", "Epoch seconds, inclusive."),
            _q("until", "number", "Epoch seconds, exclusive."),
            _q("range", "string", "Window shorthand.", values=["15m", "1h", "6h", "24h", "7d"]),
            _q("limit", "integer", "Page size, 1-200 (default 50)."),
            _q("cursor", "string", "Opaque cursor from the previous page."),
        ],
        ok=(200, "Audit page.", _page(_ref("AuditEntry"))),
        errors=(_BAD_FILTER, *_GATE_ERRORS),
        security=SEC_READ,
    ),
    # ------------------------------------------------------------------ Settings
    ("GET", "/api/v1/settings"): _op(
        "Settings",
        "getSettings",
        "Read runtime controls",
        "`config` is the safe env-driven snapshot, `settings` the raw override rows "
        "(secret-looking keys omitted) and `effective` the typed merged value of every "
        "runtime-editable control. No credential is enforced by this handler.",
        ok=(200, "Config, rows and effective controls.", _ref("SettingsPayload")),
        errors=(_BAD_FILTER, (500, "`internal_error`.")),
    ),
    ("POST", "/api/v1/settings"): _op(
        "Settings",
        "updateSettings",
        "Update runtime controls",
        "Partial update of the six runtime-editable controls; rows win over config at read "
        "time, so a write holds immediately and across restarts. Requires `settings.write` "
        "(admin+); `capture_payloads` additionally requires the owner-only "
        "`settings.capture`. Audits `settings.updated` (plus `payload_capture.toggled`) with "
        "before/after values in the same transaction and answers the full GET payload.",
        body=(
            _obj(
                {
                    "retention_traces": _int(">= 1."),
                    "retention_days": _int(">= 1."),
                    "log_ring_size": _int(">= 100."),
                    "trace_sample": _num("Between 0 and 1."),
                    "stream_tick": _int(">= 1."),
                    "capture_payloads": _bool(),
                }
            ),
            True,
            "A non-empty partial object of the runtime-editable controls only.",
        ),
        ok=(200, "The full settings payload after the write.", _ref("SettingsPayload")),
        errors=(
            _BAD_FILTER,
            (400, "`invalid_request`: unknown control, empty body, or a value out of range."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("GET", "/api/v1/ratelimits"): _op(
        "Settings",
        "getRatelimitPolicy",
        "Read the rate-limit policy",
        "Effective policy (settings rows over config defaults) plus the per-key overrides. "
        "Requires `settings.read` (viewer+).",
        ok=(200, "Policy fields plus `keys`.", _ref("RatelimitPolicy")),
        errors=(_BAD_FILTER, *_GATE_ERRORS),
        security=SEC_READ,
    ),
    ("POST", "/api/v1/ratelimits"): _op(
        "Settings",
        "updateRatelimitPolicy",
        "Update the rate-limit policy",
        "Partial, validated update. Audits `ratelimit.updated` with before/after values and "
        "stores the rows so the change binds from the next request with no restart; a "
        "`login`/`login_window` edit is pushed into the login backoff on the spot. Requires "
        "`settings.write` (admin+).",
        body=(
            _obj(
                {
                    "enabled": _bool(),
                    "engine_per_min": _int(">= 0."),
                    "ip_per_min": _int(">= 0."),
                    "mutation_per_min": _int(">= 0."),
                    "playground_per_min": _int(">= 0."),
                    "login": _int(">= 1."),
                    "login_window": {"type": "integer", "enum": [300, 900, 3600]},
                    "engine_max_inflight": _int(">= 1."),
                    "engine_queue_max": _int(">= 1."),
                    "loopback_exempt": _bool(),
                }
            ),
            True,
            "A non-empty partial object of policy fields.",
        ),
        ok=(200, "The merged policy plus `keys`.", _ref("RatelimitPolicy")),
        errors=(
            (400, "`invalid_request`: unknown field or a value outside its bound."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("GET", "/api/v1/ratelimits/usage"): _op(
        "Settings",
        "ratelimitUsage",
        "Live bucket usage",
        "Top 10 buckets by usage ratio with usage above 0, in the standard envelope. "
        "Buckets populate once enforcement wires into the request path. Requires "
        "`settings.read` (viewer+).",
        ok=(200, "Usage rows.", _page(_EVENT_ITEM)),
        errors=_GATE_ERRORS,
        security=SEC_READ,
    ),
    ("POST", "/api/v1/ratelimits/reset"): _op(
        "Settings",
        "resetRatelimitBuckets",
        "Reset buckets",
        "Clears one `{subject, scope}` bucket or every bucket with `{\"all\": true}`; the two "
        "forms are mutually exclusive. Audits `ratelimit.reset`. Requires `settings.write` "
        "(admin+).",
        body=(
            _obj(
                {
                    "subject": _str("Bucket subject."),
                    "scope": _str("Bucket scope."),
                    "all": _bool("Clear every bucket; never combined with subject/scope."),
                }
            ),
            True,
            "Exactly one of `{subject, scope}` or `{all: true}`.",
        ),
        ok=(200, "Number of cleared buckets.", _obj({"reset": _int()}, ("reset",))),
        errors=(
            (400, "`invalid_request`: both forms, neither form, or an unknown scope."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    # ------------------------------------------------------------------ Auth
    ("POST", "/api/v1/auth/login"): _op(
        "Auth",
        "login",
        "Sign in",
        "Verifies the password and sets the `lw_session` (HttpOnly) and `lw_csrf` cookies. "
        "Any previous session of the browser is revoked first, so the session id rotates on "
        "every sign-in. Audits `auth.login`, `auth.login_failed` and `auth.login_blocked`; "
        "five failures per 15 minutes per ip+email answer 429 with `Retry-After`.",
        body=(
            _obj({"email": _str(), "password": _str()}, ("email", "password")),
            True,
            "Email and password.",
        ),
        ok=(200, "The signed-in user and their permissions.", _obj(
            {
                "id": _str(),
                "email": _str(),
                "name": _str(),
                "role": _str(),
                "permissions": _arr(_str()),
            },
            ("id", "email", "role"),
        )),
        errors=(
            (400, "`invalid_request`: missing email or password."),
            (401, "`missing_or_invalid_credential`: unknown email or wrong password."),
            (429, "`rate_limited`: too many failures; `Retry-After` carries the wait."),
            (500, "`internal_error`."),
        ),
    ),
    ("POST", "/api/v1/auth/logout"): _op(
        "Auth",
        "logout",
        "Sign out",
        "Revokes the current session, audits `auth.logout` and clears the session cookie. "
        "Requires a session (an API key has no session to log out of: 401).",
        ok=(200, "Always `{\"ok\": true}`.", _obj({"ok": _bool()}, ("ok",))),
        errors=(
            (401, "`missing_or_invalid_credential`: no session, or an API-key principal."),
            (500, "`internal_error`."),
        ),
        security=SEC_WRITE,
    ),
    ("GET", "/api/v1/auth/me"): _op(
        "Auth",
        "me",
        "Current identity",
        "The signed-in user with their permission list. A session cookie is required (an "
        "API key is refused); the response re-issues the `lw_csrf` cookie.",
        ok=(200, "Current user.", _obj(
            {
                "id": _str(),
                "email": _str(),
                "name": _str(),
                "role": _str(),
                "permissions": _arr(_str()),
            },
            ("id", "email", "role"),
        )),
        errors=(
            (401, "`missing_or_invalid_credential`: no session cookie, or an API-key principal."),
            (403, "`password_change_required`: a new password is required first."),
            (500, "`internal_error`."),
        ),
        security=SEC_READ,
    ),
    ("POST", "/api/v1/auth/password"): _op(
        "Auth",
        "changePassword",
        "Change own password",
        "Verifies the current password, applies the password policy, clears `must_change` "
        "and revokes every other session of the user (audited `session.revoked_all` with "
        "`via: password_change`). The only endpoint a `must_change` user may call.",
        body=(
            _obj(
                {"current_password": _str(), "new_password": _str()},
                ("current_password", "new_password"),
            ),
            True,
            "Current password plus the new one.",
        ),
        ok=(200, "Always `{\"ok\": true}`.", _obj({"ok": _bool()}, ("ok",))),
        errors=(
            (400, "`invalid_request`: missing field, or the new password fails the policy."),
            (401, "`missing_or_invalid_credential`: no session, or the current password is wrong."),
            (500, "`internal_error`."),
        ),
        security=SEC_WRITE,
    ),
    ("POST", "/api/v1/auth/setup"): _op(
        "Auth",
        "setup",
        "First-run owner wizard",
        "Creates the first owner on an empty database and then closes permanently: once any "
        "user exists the call answers 403 `setup_closed` (serialized with a write lock, so "
        "concurrent first-runs cannot race). Audits `user.created` with `via: setup`.",
        body=(
            _obj({"email": _str(), "name": _str(), "password": _str()}, ("email", "name", "password")),
            True,
            "Owner email, display name and password.",
        ),
        ok=(200, "The created owner.", _ref("User")),
        errors=(
            (400, "`invalid_request`: missing field, or the password fails the policy."),
            (403, "`setup_closed`: setup has already been completed."),
            (500, "`internal_error`."),
        ),
    ),
    # ------------------------------------------------------------------ Users
    ("GET", "/api/v1/users"): _op(
        "Users",
        "listUsers",
        "List users",
        "Every user, oldest first, in the standard envelope. Requires `users.read` (admin+).",
        ok=(200, "User page.", _page(_ref("User"))),
        errors=(_BAD_FILTER, *_GATE_ERRORS),
        security=SEC_READ,
    ),
    ("POST", "/api/v1/users"): _op(
        "Users",
        "createUser",
        "Create a user",
        "Creates a user with `must_change` set, so the first sign-in must change the "
        "password. Requires `users.write` (owner); audits `user.created`.",
        body=(
            _obj(
                {
                    "email": _str(),
                    "name": _str(),
                    "role": _str("`owner`, `admin` or `viewer`."),
                    "password": _str("Must satisfy the password policy."),
                },
                ("email", "name", "role", "password"),
            ),
            True,
            "Email, name, role and initial password.",
        ),
        ok=(200, "The created user.", _ref("User")),
        errors=(
            (400, "`invalid_request`: missing or invalid field, weak password, duplicate email."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("PATCH", "/api/v1/users/{id}"): _op(
        "Users",
        "updateUser",
        "Update a user",
        "Partial update of `name`, `role`, `disabled` and `must_change`. Requires "
        "`users.write` (owner); audits `user.role_changed`, `user.disabled` or the matching "
        "action, and clears that account's login backoff.",
        query=[_q("id", "string", "User id (path).", required=True)],
        body=(
            _obj(
                {
                    "name": _str(),
                    "role": _str("`owner`, `admin` or `viewer`."),
                    "disabled": _bool(),
                    "must_change": _bool(),
                }
            ),
            True,
            "A non-empty partial object of those four fields.",
        ),
        ok=(200, "The updated user.", _ref("User")),
        errors=(
            (400, "`invalid_request`: unknown field, empty body, or an invalid role."),
            (404, "`not_found`: no such user."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("DELETE", "/api/v1/users/{id}"): _op(
        "Users",
        "deleteUser",
        "Delete a user",
        "Deletes a user; the last owner and the acting user itself are refused. Requires "
        "`users.write` (owner); audits `user.deleted`.",
        query=[_q("id", "string", "User id (path).", required=True)],
        ok=(200, "Deleted.", _obj({"id": _str(), "deleted": _bool()}, ("id", "deleted"))),
        errors=(
            (400, "`invalid_request`: the last owner or the acting user cannot be deleted."),
            (404, "`not_found`: no such user."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("GET", "/api/v1/users/{id}/sessions"): _op(
        "Users",
        "listUserSessions",
        "Active sessions",
        "Unrevoked, unexpired sessions of one user. Requires `sessions.read` (owner).",
        query=[_q("id", "string", "User id (path).", required=True)],
        ok=(200, "Session page.", _page(_ref("Session"))),
        errors=((404, "`not_found`: no such user."), *_GATE_ERRORS),
        security=SEC_READ,
    ),
    ("DELETE", "/api/v1/sessions"): _op(
        "Users",
        "revokeOtherSessions",
        "Revoke other sessions",
        "Revokes every session except the caller's own. Requires `sessions.revoke_all` "
        "(owner); audits `session.revoked_all` with `scope: all`.",
        ok=(200, "Number of revoked sessions.", _obj({"revoked": _int()}, ("revoked",))),
        errors=_GATE_ERRORS,
        security=SEC_WRITE,
    ),
    # ------------------------------------------------------------------ API keys
    ("GET", "/api/v1/keys"): _op(
        "API keys",
        "listKeys",
        "List keys",
        "Every key including revoked ones, newest first; only the 8-character prefix is "
        "exposed, never the secret. Requires `keys.list` (viewer+).",
        ok=(200, "Key page.", _page(_ref("KeyRecord"))),
        errors=(_BAD_FILTER, *_GATE_ERRORS),
        security=SEC_READ,
    ),
    ("POST", "/api/v1/keys"): _op(
        "API keys",
        "createKey",
        "Create a key",
        "Creates a key and returns the plaintext secret exactly once. `name` is required "
        "(trimmed to 120 characters); `rate_limit_per_min` and `burst` are optional "
        "overrides. Requires `keys.write` (admin+); audits `key.created`.",
        body=(
            _obj(
                {
                    "name": _str("Operator label."),
                    "rate_limit_per_min": _nullable(_int("Override; null uses the policy.")),
                    "burst": _nullable(_int("Override; null uses the policy.")),
                },
                ("name",),
            ),
            True,
            "`name` required; the two limit fields are optional overrides.",
        ),
        ok=(200, "The key plus its one-time secret.", _obj(
            {
                "id": _str(),
                "name": _str(),
                "key": _str("Plaintext secret, shown once."),
                "prefix": _str(),
                "rate_limit_per_min": _nullable(_int()),
                "burst": _nullable(_int()),
                "note": _str("Store this key now; it is not shown again."),
            },
            ("id", "name", "key"),
        )),
        errors=(
            (400, "`invalid_request`: missing name or a bad limit value."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("DELETE", "/api/v1/keys"): _op(
        "API keys",
        "revokeKeyByBody",
        "Revoke a key (body)",
        "Revokes the key named by the JSON body. Same semantics as the path form: soft "
        "delete (the row stays listed as revoked), rotation state cleared, audits "
        "`key.revoked`. Requires `keys.write` (admin+).",
        body=(_obj({"id": _str("Key id.")}, ("id",)), True, "The key id."),
        ok=(200, "Revoked.", _obj({"id": _str(), "revoked": _bool()}, ("id", "revoked"))),
        errors=(
            (400, "`invalid_request`: missing `id`."),
            (404, "`not_found`: no such key."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("DELETE", "/api/v1/keys/{id}"): _op(
        "API keys",
        "revokeKey",
        "Revoke a key",
        "Soft delete: the row stays in the list as revoked, the rotation grace state is "
        "cleared, and audits `key.revoked`. Requires `keys.write` (admin+).",
        query=[_q("id", "string", "Key id (path).", required=True)],
        ok=(200, "Revoked.", _obj({"id": _str(), "revoked": _bool()}, ("id", "revoked"))),
        errors=((404, "`not_found`: no such key."), *_GATE_ERRORS),
        security=SEC_WRITE,
    ),
    ("POST", "/api/v1/keys/{id}/rotate"): _op(
        "API keys",
        "rotateKey",
        "Rotate a key",
        "Issues a new secret and keeps the previous one valid for a 300-second grace "
        "window; a revoked key cannot be rotated. Returns the new secret once. Requires "
        "`keys.write` (admin+); audits `key.rotated`.",
        query=[_q("id", "string", "Key id (path).", required=True)],
        ok=(200, "The new secret.", _obj(
            {
                "id": _str(),
                "key": _str("Plaintext secret, shown once."),
                "prefix": _str(),
                "grace": _int("Seconds the previous secret stays valid."),
                "note": _str("Store this key now; it is not shown again."),
            },
            ("id", "key"),
        )),
        errors=(
            (400, "`invalid_request`: cannot rotate a revoked key."),
            (404, "`not_found`: no such key."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("PATCH", "/api/v1/keys/{id}"): _op(
        "API keys",
        "setKeyLimits",
        "Set per-key limits",
        "Sets `rate_limit_per_min` and/or `burst` for one key; `null` clears an override so "
        "the policy applies again. At least one limit field is required and unknown fields "
        "are refused. Requires `keys.write` (admin+); audits `ratelimit.updated` with "
        "before/after values.",
        query=[_q("id", "string", "Key id (path).", required=True)],
        body=(
            _obj({"rate_limit_per_min": _nullable(_int()), "burst": _nullable(_int())}),
            True,
            "At least one of the two limit fields; `null` clears the override.",
        ),
        ok=(200, "The stored limits.", _obj(
            {
                "id": _str(),
                "rate_limit_per_min": _nullable(_int()),
                "burst": _nullable(_int()),
            },
            ("id",),
        )),
        errors=(
            (400, "`invalid_request`: unknown field, or no limit field present."),
            (404, "`not_found`: no such key."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    ("GET", "/api/v1/keys/auth"): _op(
        "API keys",
        "getKeyAuth",
        "Read the key-auth switch",
        "Whether engine key verification is armed. Requires `keys.read_auth` (viewer+).",
        ok=(200, "The switch state.", _obj({"enabled": _bool()}, ("enabled",))),
        errors=(_BAD_FILTER, *_GATE_ERRORS),
        security=SEC_READ,
    ),
    ("POST", "/api/v1/keys/auth"): _op(
        "API keys",
        "setKeyAuth",
        "Arm or disarm key auth",
        "Turns engine key verification on or off. Arming is refused while no active key "
        "exists. Requires `keys.write` (admin+); audits `key.auth_changed`.",
        body=(_obj({"enabled": _bool()}, ("enabled",)), True, "The desired state."),
        ok=(200, "The stored state.", _obj({"enabled": _bool()}, ("enabled",))),
        errors=(
            (400, "`invalid_request`: `enabled` is not a boolean, or no active key exists."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
    # ------------------------------------------------------------------ Models
    ("GET", "/api/v1/models"): _op(
        "Models",
        "listModels",
        "Checkpoint inventory",
        "Loaded, available and default checkpoints with device, RSS and the per-checkpoint "
        "`stats` block. Reads SQLite only and never touches the engine. No credential is "
        "enforced by this handler.",
        ok=(200, "Inventory and stats.", _obj(
            {
                "loaded": _arr(_str()),
                "available": _arr(_str()),
                "default": _arr(_str()),
                "device": _str(),
                "rss_mb": _num(),
                "stats": _obj({}, ()),
            },
            ("loaded", "available"),
        )),
        errors=((500, "`internal_error`."),),
    ),
    ("POST", "/api/v1/models"): _op(
        "Models",
        "mutateModels",
        "Load or unload by action",
        "Generic form of the two explicit endpoints: the body carries `action` (`load` or "
        "`unload`) plus `models`. Serialized with the other model mutations, audited "
        "`model.load`/`model.unload` and published on the stream. No credential is enforced "
        "by this handler.",
        body=(
            _obj(
                {
                    "action": _str("`load` or `unload`."),
                    "models": _arr(_str("Non-empty checkpoint names.")),
                },
                ("action", "models"),
            ),
            True,
            "`action` and a non-empty `models` list.",
        ),
        ok=(200, "The loaded set after the change.", _obj({"loaded": _arr(_str())}, ("loaded",))),
        errors=(
            (400, "`invalid_request`: `action` is not load/unload, or `models` is empty."),
            (409, "`conflict`: blocked by `LAYA_ENGLISH_ONLY` or a load already in flight."),
            (500, "`internal_error`."),
        ),
    ),
    ("POST", "/api/v1/models/load"): _op(
        "Models",
        "loadModels",
        "Load checkpoints",
        "Loads the named checkpoints. Refused with 409 while an english-only deployment "
        "blocks the checkpoint or another load is in flight. No credential is enforced by "
        "this handler.",
        body=(
            _obj({"models": _arr(_str("Non-empty checkpoint names."))}, ("models",)),
            True,
            "A non-empty `models` list.",
        ),
        ok=(200, "The loaded set after the change.", _obj({"loaded": _arr(_str())}, ("loaded",))),
        errors=(
            (400, "`invalid_request`: `models` missing or empty."),
            (409, "`conflict`: blocked by `LAYA_ENGLISH_ONLY` or a load already in flight."),
            (500, "`internal_error`."),
        ),
    ),
    ("POST", "/api/v1/models/unload"): _op(
        "Models",
        "unloadModels",
        "Unload checkpoints",
        "Unloads the named checkpoints and refuses to unload the last loaded one. No "
        "credential is enforced by this handler.",
        body=(
            _obj({"models": _arr(_str("Non-empty checkpoint names."))}, ("models",)),
            True,
            "A non-empty `models` list.",
        ),
        ok=(200, "The loaded set after the change.", _obj({"loaded": _arr(_str())}, ("loaded",))),
        errors=(
            (400, "`invalid_request`: `models` missing or empty."),
            (409, "`conflict`: refuses to unload the last loaded checkpoint."),
            (500, "`internal_error`."),
        ),
    ),
    # ------------------------------------------------------------------ Playground
    ("GET", "/api/v1/playground/templates"): _op(
        "Playground",
        "listPlaygroundTemplates",
        "Built-in templates",
        "The three canonical templates (department, urgency, churn) with the question specs "
        "the playground seeds its editor with. Any authenticated principal (viewer+).",
        ok=(200, "Template list.", _obj({"items": _arr(_obj({}, ()))}, ("items",))),
        errors=(_BAD_FILTER, *_GATE_ERRORS),
        security=SEC_READ,
    ),
    ("POST", "/api/v1/playground/run"): _op(
        "Playground",
        "runPlayground",
        "Run a prediction in-process",
        "Mirrors `POST /predict` in-process: same body shape, same rejection mapping, same "
        "response shape. Records one trace with `meta.source = \"playground\"`, so the run "
        "appears in the trace list within one stream tick and its `X-Request-Id` (the trace "
        "id) links the status badge to the detail page. Requires `playground.run` (admin+).",
        body=(
            _ref("PredictRequest"),
            True,
            "Same shape as `POST /predict`.",
        ),
        ok=(200, "The adapter's answer payload.", _ref("PredictResponse")),
        errors=(
            (400, "`invalid_request`: missing `state`/`questions`, or the engine rejected the call."),
            (422, "`english_only`: non-English state in an english-only deployment."),
            *_GATE_ERRORS,
        ),
        security=SEC_WRITE,
    ),
}


def build() -> dict:
    """Assemble the OpenAPI 3.1 document from :data:`OPERATIONS`."""
    paths: dict[str, dict[str, Any]] = {}
    for (method, path), operation in OPERATIONS.items():
        paths.setdefault(path, {})[method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {
            "title": TITLE,
            "version": layawatch.__version__,
            "description": DESCRIPTION,
        },
        "servers": [{"url": "/", "description": "Same origin as the console."}],
        "tags": [{"name": name, "description": note} for name, note in TAGS],
        "paths": dict(sorted(paths.items())),
        "components": {"schemas": SCHEMAS, "securitySchemes": SECURITY_SCHEMES},
    }


def documented_paths() -> frozenset[str]:
    """Every path template the table documents."""
    return frozenset(path for _method, path in OPERATIONS)


def undocumented(routes: Iterable[tuple[str, str]]) -> list[str]:
    """``"METHOD pattern"`` entries from a router table this document does not cover.

    A trailing-``*`` prefix counts as covered when at least one documented path sits under
    it; ``/*`` is the console's static catch-all and is never part of the API surface.
    """
    covered = documented_paths()
    missing: list[str] = []
    for method, pattern in routes:
        if pattern == "/*":
            continue
        if (method, pattern) in OPERATIONS:
            continue
        if pattern.endswith("*") and any(path.startswith(pattern[:-1]) for path in covered):
            continue
        missing.append(f"{method} {pattern}")
    return sorted(missing)
