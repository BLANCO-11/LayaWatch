# LayaWatch rate limiting

Version: 0.1 (2026-09-22)
Scope: this document defines the policy model, enforcement, observability and management surface for
rate limiting. It is a first-class product feature, not just a security control.

## 1. Why it is managed, not hardcoded

The operator owns the limits. Every limit is visible, editable, audited and observable:

- **Visible**: current bucket usage per subject appears in the UI, and throttling shows up in charts.
- **Editable**: global defaults, per-surface limits and per-key overrides are editable at runtime
  (no restart, no config file edit) by `owner` and `admin`.
- **Audited**: every policy change and every manual bucket reset is an audit entry.
- **Observable**: throttled requests are recorded as traces with `status = 429`, so a rate limit
  shows as a distinct line in the Metrics view instead of invisible rejection.

## 2. Policy model

A policy is a tuple: **subject x scope x limit**.

| Subject | Scope | Default limit | Notes |
|---|---|---|---|
| API key | `POST /predict`, `POST /route` | unlimited (`0` = off) | per-key override on the `api_keys` row |
| IP address | engine endpoints | 600 / min | catches unauthenticated floods before auth work |
| IP + email | `POST /api/v1/auth/login` | 5 / 15 min, then exponential backoff | anti credential stuffing |
| Session | `/api/v1/*` mutations | 60 / min | protects against runaway UI loops |
| Session | `POST /api/v1/playground/run` | 30 / min | inference is expensive |
| Session | SSE connections | 5 concurrent | connection cap, not a rate |
| Global | concurrent forward passes | `LAYWATCH_ENGINE_MAX_INFLIGHT` (default 4) | queue depth cap; excess is shed with 429 plus `Retry-After` |

Bucket semantics:

- Token bucket, capacity `burst` (default = limit), refill `limit / window` tokens per second.
- State is in memory, keyed by subject id, swept every 60 s. Single process, so no shared store is
  needed; a restart resets buckets, which is documented and acceptable.
- Clock is `time.monotonic()`, so wall-clock jumps cannot bypass or freeze a bucket.
- Fairness is FIFO per bucket: a rejected request does not consume a token, and an accepted request
  consumes exactly one.
- Login backoff is separate: after the 5th failure the subject waits `2^(n-5)` seconds, capped at
  15 minutes, and the wait is reset by a successful login or by an owner action.

Exemptions: `owner` and `admin` sessions are exempt from the mutation limit; loopback requests are
exempt from the IP limit unless `LAYWATCH_RATELIMIT_LOOPBACK=1`. Exemptions are listed in the UI so
they are never a surprise.

## 3. Enforcement points

| Point | Layer | Behavior |
|---|---|---|
| Before body read | middleware | IP bucket check for engine endpoints; reject early with `429` and `Content-Length: 0` |
| After auth | middleware | key bucket check with per-key override |
| Before mutation handlers | middleware | session mutation bucket |
| Before engine forward | engine adapter | global inflight semaphore with bounded wait (`LAYWATCH_ENGINE_QUEUE_MAX`, default 16); beyond the bound, shed with `429` |
| Login handler | api/auth | failure counter plus backoff |
| SSE handler | api/stream | connection cap per session, oldest closed with a `bye` event |

Shedding vs throttling: **throttling** (per-subject bucket) means "you are too fast"; **shedding**
(global inflight cap) means "the engine is saturated". Both return `429`, but the error codes differ
(`rate_limited` vs `engine_saturated`) and the UI shows them as separate series.

**Status**: enforcement is phase-scoped out. The management surface (7) is live and writes real
`settings` rows, but bucket checks do not yet run in the request path - the usage/reset buckets stay
empty until enforcement wires in. The login backoff row (api/auth, `auth.login_limit`) is live and
applies `login`/`login_window` edits on the spot.

## 4. Response contract

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 12
RateLimit-Limit: 600
RateLimit-Remaining: 0
RateLimit-Reset: 12
Content-Type: application/json

{"error":{"code":"rate_limited","message":"Rate limit exceeded for this API key. Retry in 12 seconds.","details":{"scope":"api_key","limit":600,"window":"60s"}}}
```

- `Retry-After` and `RateLimit-Reset` are seconds, ceiling-rounded.
- `RateLimit-*` headers are sent on every engine response (not only 429) so clients can self-throttle.
- Successful responses never omit the headers once the feature is enabled.

## 5. Observability of limits

- Throttled requests are recorded as traces with `status = 429`, `error_code = rate_limited` or
  `engine_saturated`, and a `rate_limit` span carrying `scope`, `limit`, `remaining`.
- Rollups add a `throttled` metric so the Metrics view can plot throttling next to errors.
- `/api/v1/meta` reports `ratelimit_blocks_total` and `engine_shed_total`.
- The Settings view shows live bucket usage for the top 10 subjects (subject, scope, used, limit,
  reset in), refreshed on the SSE tick. This is how an operator answers "who is being throttled".
- The Overview KPI strip gains a `Throttled (5m)` cell only when the count is non-zero, so a healthy
  deployment does not carry a permanent zero.

## 6. Management surface

### 6.1 Settings view, "Rate limits" card

| Control | Type | Effect |
|---|---|---|
| Enable rate limiting | switch | master switch (`ratelimit.enabled`, read per request) |
| Engine per key | number, `0` = unlimited | default for new keys |
| Engine per IP | number | unauthenticated flood guard |
| Login attempts | number per window | with a window select (`5m`, `15m`, `1h`) |
| Mutation per session | number per minute | UI protection |
| Playground runs | number per minute | inference protection |
| Engine inflight cap | number | semaphore size |
| Engine queue bound | number | beyond this, shed |
| Exempt loopback | switch | whether 127.0.0.1 bypasses IP limits |

Changing any value writes an audit entry `ratelimit.updated` with before and after values, and takes
effect on the next request (no restart).

### 6.2 Keys view

The keys table gains a `Limit` column (mono, `unlimited` or `600/min`) and a row action
`Set limit` opening a dialog with limit, burst and a `Reset usage` action (audited as
`ratelimit.reset`). The create-key dialog includes the same fields with the global default prefilled.

### 6.3 Users view

Role-based exemption is shown per user row (`exempt: admin`), read-only, derived from the role. There
is no per-user limit in v0.1.

### 6.4 Live buckets panel

A flat table under the Rate limits card: `subject`, `scope`, `used / limit`, `resets in`. Only
subjects with usage above 0 in the current window are listed, sorted by usage ratio. Actions: `Reset`
per row. This panel is the reason limits are described as "managed".

## 7. API

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/ratelimits` | viewer+ | effective policy (settings rows over config defaults) plus per-key overrides (`keys`) |
| `POST` | `/api/v1/ratelimits` | admin+ | partial update of the policy fields (validated, audited `ratelimit.updated`) |
| `GET` | `/api/v1/ratelimits/usage` | viewer+ | `{items: [{subject, scope, used, limit, resets_in}], next_cursor, total_estimate}`, top 10 by usage ratio |
| `POST` | `/api/v1/ratelimits/reset` | admin+ | `{subject, scope}` or `{all: true}` resets buckets, answers `{"reset": n}` (audited `ratelimit.reset`) |
| `PATCH` | `/api/v1/keys/{id}` | admin+ | set `rate_limit_per_min` and `burst` on a key |

## 8. Configuration defaults

| Variable | Default | Purpose |
|---|---|---|
| `LAYWATCH_RATELIMIT_ENABLED` | `1` | master switch |
| `LAYWATCH_RATELIMIT_ENGINE_PER_MIN` | `0` | per-key default; `0` means unlimited |
| `LAYWATCH_RATELIMIT_IP_PER_MIN` | `600` | per-IP engine guard |
| `LAYWATCH_RATELIMIT_LOGIN` | `5` | login attempts per window |
| `LAYWATCH_RATELIMIT_LOGIN_WINDOW` | `900` | login window, seconds |
| `LAYWATCH_RATELIMIT_MUTATION_PER_MIN` | `60` | session mutations |
| `LAYWATCH_RATELIMIT_PLAYGROUND_PER_MIN` | `30` | playground runs |
| `LAYWATCH_RATELIMIT_SSE_PER_SESSION` | `5` | concurrent streams |
| `LAYWATCH_RATELIMIT_LOOPBACK` | `0` | apply IP limits to loopback |
| `LAYWATCH_ENGINE_MAX_INFLIGHT` | `4` | forward passes allowed to queue |
| `LAYWATCH_ENGINE_QUEUE_MAX` | `16` | hard queue bound before shedding |

Environment variables provide defaults; values saved in `settings` win. The UI marks each field as
`from env` (locked) or `editable`.

## 9. Storage

- Policy lives in `settings` (`ratelimit.*` keys) with `updated_at`, `updated_by`.
- Per-key overrides live on `api_keys` (`rate_limit_per_min INTEGER`, `burst INTEGER`), both nullable
  meaning "use the global default".
- Bucket state is memory only. `ratelimit_blocks_total` and `engine_shed_total` are counters, reset on
  restart, and also persisted per rollup bucket through the `throttled` metric so history survives.

## 10. Non-goals

- No distributed rate limiting (no Redis). One process owns one bucket set.
- No per-tenant quotas, no billing, no tiered plans.
- No request shaping or delay injection; exceeding a limit is always a fast rejection.
- No global concurrency limit on `/api/v1` reads; SQLite reads are cheap and bounded by the cursor.

## 11. Acceptance criteria (Phase 3 gate)

1. Setting the per-key limit to 3/min and issuing 5 requests yields 3 x `200` and 2 x `429` with
   `Retry-After`, and the 429s appear as traces with `error_code = rate_limited`.
2. `RateLimit-*` headers are present on successful engine responses and reflect remaining tokens.
3. Login with a wrong password 6 times returns `429` on the 6th with an increasing `Retry-After`, and
   the audit log records `auth.login_failed` for the first 5 and `auth.login_blocked` for the 6th.
4. Filling the engine queue beyond `LAYWATCH_ENGINE_QUEUE_MAX` yields `engine_saturated`, distinct
   from `rate_limited` in traces and in the Metrics view.
5. Changing a limit in Settings takes effect on the next request without a restart, and writes an
   audit entry with before and after values.
6. `POST /api/v1/ratelimits/reset` clears a bucket, verified by an immediate successful request that
   would otherwise be throttled, plus an audit entry.
7. Loopback exemption toggles behavior as configured, verified by test.
8. Bucket sweep does not leak memory: 10,000 distinct subjects then a sweep leaves the map bounded
   (asserted by a unit test on the sweeper).
