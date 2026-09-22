# LayaWatch API reference

Version: 0.1 (2026-09-22)
Base path: `/api/v1`. All responses are JSON unless stated. All timestamps are epoch seconds with
millisecond precision unless a field name says otherwise.

## 1. Authentication

| Caller | Credential | Applies to |
|---|---|---|
| Engine clients | `X-API-Key: lay_...` or `Authorization: Bearer lay_...` | `POST /predict`, `POST /route` |
| Console (browser) | session cookie `lw_session` (HttpOnly, SameSite=Lax, Secure behind TLS) | all `/api/v1/*` except login and health |
| Automation | `X-Admin-Token: <LAYA_ADMIN_TOKEN>` (legacy) | `/api/v1/*` mutations while the variable is set |
| CI | API key with role `viewer` scoped to reads | `GET /api/v1/*` |

Mutations from a browser session require the CSRF header `X-CSRF-Token` matching the `lw_csrf` cookie
(double submit). Mutations from an API key or the legacy admin token do not.

Status codes: `401` missing or invalid credential, `403` authenticated but not permitted, `429` rate
limited with `Retry-After`.

## 2. Conventions

- **Error envelope**

```json
{ "error": { "code": "invalid_filter", "message": "status must be an integer", "details": {"field": "status"} } }
```

Codes are stable snake_case identifiers. `message` is human readable, no stack traces, no internals.

- **Pagination** is cursor based: `?limit=50&cursor=<opaque>`. Responses carry
  `{"items": [...], "next_cursor": "...", "total_estimate": 1204}`. `limit` max 200, default 50.
- **Filtering** is explicit: unknown query parameters are a `400 invalid_filter`, never ignored.
- **Time ranges** use `?since=<epoch>&until=<epoch>` or a shorthand `?range=15m` (`15m`, `1h`, `6h`,
  `24h`, `7d`). `step` is server-chosen unless explicitly allowed.
- **Request id** is returned on every response as `X-Request-Id` (8 hex) and equals the trace id for
  engine endpoints.
- **Compression**: responses over 1 KB honor `Accept-Encoding: gzip`.
- **Rate limit headers**: every engine response carries `RateLimit-Limit`, `RateLimit-Remaining` and
  `RateLimit-Reset`; rejections add `Retry-After` and use the `rate_limited` or `engine_saturated`
  error codes. Policy, defaults and the management surface are defined in `docs/rate-limiting.md`.

## 3. Health and meta

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/healthz` | none | liveness: `{"status":"ok","models":[...],"device":"auto","auth_enabled":true}` |
| `GET` | `/api/v1/meta` | session or key | version, uptime, started_at, config snapshot (safe fields only), self-observability counters |
| `GET` | `/api/v1/settings` | session (any role) | effective settings, read-only view for viewers |
| `POST` | `/api/v1/settings` | owner, admin | update mutable settings (retention, sampling, capture, stream tick) |

## 4. Engine

| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/predict` | API key when auth is armed | body `{state, questions, model?, task?, lang?, temperature?}`; returns `{answers, model, route_reason, ...}` |
| `POST` | `/route` | API key when auth is armed | routing decision without a forward pass |
| `GET` | `/` | none | service info: `{service, version, docs, ui}` |

Both endpoints record a trace. `X-Request-Id` echoes the trace id; a client may supply
`X-Request-Id` to set it (8 hex, validated).

## 5. Traces

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/traces` | list, newest first |
| `GET` | `/api/v1/traces/{id}` | one trace with observations, scores and related log lines |
| `GET` | `/api/v1/traces/{id}/observations` | spans only, for lazy loading the waterfall |
| `POST` | `/api/v1/traces/{id}/scores` | attach a score |
| `POST` | `/api/v1/traces/{id}/tags` | add or remove tags |
| `DELETE` | `/api/v1/traces/{id}` | delete a single trace (owner, admin) |

Query parameters for the list: `route`, `status` (integer or class `4xx`/`5xx`), `model`, `q`
(substring match on id or error message), `min_duration_ms`, `tag`, `since`/`until`/`range`, `limit`,
`cursor`, `order` (`newest` default, `slowest`).

Trace summary item:

```json
{
  "id": "9f2c1a4b",
  "ts_start": 1790080267.284,
  "duration_ms": 412.5,
  "route": "/predict",
  "method": "POST",
  "status": 200,
  "model": "english",
  "route_reason": "state is English",
  "queue_ms": 5.2,
  "forward_ms": 391.4,
  "client_key_id": "k_3f8a",
  "span_count": 9,
  "error_code": null
}
```

Trace detail adds `observations[]`, `scores[]`, `logs[]` and `meta`.

## 6. Metrics

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/metrics` | rollup series for one or more metrics |
| `GET` | `/api/v1/metrics/summary` | KPI strip values plus deltas against the previous window |
| `GET` | `/api/v1/metrics/models` | model mix buckets for the stacked bar chart |

`/api/v1/metrics?metrics=requests,latency,errors,queue&range=15m&model=english` returns:

```json
{
  "step": 10,
  "window": {"since": 1790079367.0, "until": 1790080267.0},
  "series": [
    {"metric": "requests", "points": [[1790079367, 3.8], [1790079377, 4.1]]},
    {"metric": "latency", "points": [[1790079367, {"p50": 412.5, "p90": 631.0, "p95": 863.1}]]},
    {"metric": "errors", "points": [[1790079367, 2]]},
    {"metric": "queue", "points": [[1790079367, 5.2]]}
  ],
  "note": "percentiles are bucket-level"
}
```

## 7. Logs

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/logs?level=&q=&trace_id=&limit=&cursor=` | tail, newest first |
| `GET` | `/api/v1/logs/export?range=1h&format=jsonl` | download (owner, admin) |

## 8. Live stream (SSE)

`GET /api/v1/stream` with `Accept: text/event-stream`. Session cookie or API key required. Events:

| Event | Payload | Cadence |
|---|---|---|
| `hello` | `{server_time, tick, ring_size}` | on connect |
| `pulse` | `{requests_per_s, errors_5m, p50, p95, queue_ms, rss_mb, dropped_total, queue_depth}` | every tick (default 3 s) |
| `trace` | trace summary item | as traces complete |
| `log` | log entry | as lines are recorded |
| `model` | `{loaded: [...], action: "load"|"unload"}` | on change |
| `ping` | `{}` | every 15 s to keep proxies open |

Framing: standard SSE (`event:`, `data:`, blank line). On reconnect the client sends
`Last-Event-ID`; the server replays at most 200 missed trace events from the ring, then resumes live.

## 9. API keys

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/keys` | viewer+ | list (id, name, prefix, created, last_used, request_count, revoked) |
| `POST` | `/api/v1/keys` | admin+ | create, returns the secret once |
| `POST` | `/api/v1/keys/{id}/rotate` | admin+ | rotate, returns the new secret once |
| `DELETE` | `/api/v1/keys/{id}` | admin+ | revoke (soft delete, kept in the list as revoked) |
| `POST` | `/api/v1/keys/auth` | admin+ | arm or disarm key requirement for `/predict` and `/route` |

Secrets are `lay_` plus 32 base62 characters, stored as HMAC-SHA256 with a server pepper from
`secret.key`. The plaintext is never retrievable; only `prefix` (first 8) is listed.

### 9.1 Per-key limits and the policy surface

| Method | Path | Role | Purpose |
|---|---|---|---|
| `PATCH` | `/api/v1/keys/{id}` | admin+ | set `rate_limit_per_min` and `burst` for one key (`null` clears the override) |
| `GET` | `/api/v1/ratelimits` | viewer+ | effective policy plus per-key overrides |
| `POST` | `/api/v1/ratelimits` | admin+ | partial update of policy fields, validated and audited |
| `GET` | `/api/v1/ratelimits/usage` | viewer+ | live bucket usage for the management panel |
| `POST` | `/api/v1/ratelimits/reset` | admin+ | reset one subject's bucket or all buckets (audited) |

Policy model, defaults, enforcement points, response contract and acceptance criteria:
`docs/rate-limiting.md`.

## 10. Users and sessions

| Method | Path | Role | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | none | `{email, password}` sets `lw_session` and `lw_csrf`; 401 on failure, 429 after 5 failures per 15 min per ip+email |
| `POST` | `/api/v1/auth/logout` | session | revoke current session |
| `GET` | `/api/v1/auth/me` | session | current user, role, permissions |
| `POST` | `/api/v1/auth/password` | session | change own password (requires current password) |
| `GET` | `/api/v1/users` | admin+ | list users |
| `POST` | `/api/v1/users` | owner | create user `{email, name, role, password}` |
| `PATCH` | `/api/v1/users/{id}` | owner | change name, role, disabled, force password change |
| `DELETE` | `/api/v1/users/{id}` | owner | delete user (cannot delete the last owner or self) |
| `GET` | `/api/v1/users/{id}/sessions` | owner | active sessions |
| `DELETE` | `/api/v1/sessions` | owner | revoke all sessions except the current one |

Roles: `owner` (everything, including user management and settings), `admin` (keys, models,
retention, trace deletion, playground), `viewer` (read-only: traces, metrics, logs, models, keys list).

Bootstrap: on an empty database, if `LAYWATCH_BOOTSTRAP_OWNER=email:password` is set, the owner is
created on first start and the variable is ignored afterwards. Without it, `/api/v1/auth/setup`
creates the first owner through the UI wizard and then closes permanently.

## 11. Models

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/models` | viewer+ | `{loaded: [...], available: [...], default: [...], device, rss_mb}` |
| `POST` | `/api/v1/models/load` | admin+ | `{models: ["multilingual"]}`; 409 when blocked by `LAYA_ENGLISH_ONLY` or a load is in flight |
| `POST` | `/api/v1/models/unload` | admin+ | `{models: [...]}`; refuses to unload the last loaded checkpoint |

Load and unload are serialized, audited, and take effect immediately; the stream emits a `model` event.

## 12. Playground

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/playground/templates` | viewer+ | built-in templates (department, urgency, churn) with their questions |
| `POST` | `/api/v1/playground/run` | admin+ | `{state, questions, model?, task?, lang?, temperature?}` runs in-process, records a trace with `meta.source = "playground"` and returns the same shape as `/predict` |

## 13. Audit

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/audit?actor=&action=&range=&limit=&cursor=` | admin+ | append-only audit entries |

Recorded actions include `auth.login`, `auth.login_failed`, `auth.logout`, `key.created`,
`key.rotated`, `key.revoked`, `key.auth_changed`, `model.loaded`, `model.unloaded`, `user.created`,
`user.role_changed`, `user.disabled`, `user.deleted`, `session.revoked_all`, `settings.updated`,
`trace.deleted`, `payload_capture.toggled`.

## 14. Legacy compatibility

| Legacy | Behavior in v0.1 | Removal |
|---|---|---|
| `GET /admin` | `302` to `/` | Phase 8 |
| `GET /admin/api/stats` | mapped to `/api/v1/metrics/summary` shape | Phase 8 |
| `GET /admin/api/logs` | mapped to `/api/v1/logs` | Phase 8 |
| `GET/POST/DELETE /admin/api/keys` | mapped to `/api/v1/keys` | Phase 8 |
| `GET/POST /admin/api/auth` | mapped to `/api/v1/keys/auth` | Phase 8 |
| `GET/POST /admin/api/models` | mapped to `/api/v1/models` | Phase 8 |
| `api_keys.json` | imported once into SQLite, file renamed `api_keys.json.imported` | after import |

Shim responses carry `Deprecation: true` and `Link: </api/v1/...>; rel="successor-version"`. The
shims are deleted at Phase 8; until then every shim call increments a counter shown in Settings so the
operator can see whether anything still depends on them.
