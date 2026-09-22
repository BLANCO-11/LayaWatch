# Phase 3 - Auth, users, keys and rate limits

Status: planned
Depends on: phase 2
Estimated effort: 5 to 7 days
Deliverable: real accounts with roles, hashed sessions, CSRF-protected mutations, audited user and
key management, and managed rate limiting enforcing all limits in `docs/rate-limiting.md` end to end.

## Objective

Make access real: users log in with scrypt-hashed passwords, every mutation is authenticated,
CSRF-checked and audited in the same transaction, API keys are issued and rotated safely, and the
rate limit policy from `docs/rate-limiting.md` is enforced, observable and editable at runtime.

## Scope

In: password hashing and policy, session cookies and rotation, CSRF double-submit, user CRUD with
roles and invariants, first-run setup (`/api/v1/auth/setup` and `LAYWATCH_BOOTSTRAP_OWNER`), API key
issue/rotate/revoke/arm with per-key limits, token buckets with sweeper and login backoff, engine
inflight semaphore with queue bound and `engine_saturated` shedding, `RateLimit-*` and `Retry-After`
headers, 429 traces with `rate_limit` span, runtime-editable policy in `settings`, live usage
endpoint, audited resets, middleware role gates for the existing mutation handlers.

Out: login and setup UI (Phase 4, backend endpoints only here), Settings and Keys UI cards (Phase 6),
any new observability read views (Phase 5), packaging (Phase 8), distributed rate limiting
(non-goal), composition rules or forced password rotation (excluded by `docs/security.md` 3.1).

## Deliverables

```
layawatch/auth/passwords.py    scrypt hash/verify, policy checks, top-10k list lookup
layawatch/auth/sessions.py     lw_session issue, hash-at-rest lookup, TTL, rotation, revoke-all
layawatch/auth/csrf.py         lw_csrf double-submit token issue and verify
layawatch/auth/users.py        user CRUD, role checks, last-owner and self-protection invariants
layawatch/auth/ratelimit.py    token buckets, sweeper, login backoff, inflight semaphore, counters
layawatch/auth/audit.py        append-only audit helper, same-transaction writes
layawatch/api/auth.py          login, logout, me, password change, setup endpoint
layawatch/api/users.py         user list, create, patch, delete, sessions list, revoke-all
layawatch/api/ratelimits.py    GET/POST policy, usage, reset endpoints
layawatch/api/keys.py          key CRUD, rotate, revoke, arm/disarm, per-key limit PATCH
layawatch/http/middleware.py   role gate, CSRF gate, per-subject bucket checks, RateLimit headers
data/common_passwords.txt      built-in top-10k list shipped with the repo
tests/                         password, session, csrf, user, ratelimit, login-ux tests
```

## Tasks

1. `layawatch/auth/passwords.py`: `hashlib.scrypt` with `n=2**14, r=8, p=1, dklen=32`, 16-byte
   random salt, stored as `scrypt$n$r$p$salt_b64$hash_b64`, verify with `hmac.compare_digest`.
   Policy per `docs/security.md` 3.1: minimum 12 characters, reject if in `data/common_passwords.txt`
   (top 10k, shipped with the repo), reject if equal to the email local part, no composition rules.
2. `layawatch/auth/sessions.py`: 32-byte random `lw_session` token (`secrets.token_urlsafe`),
   `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` when `X-Forwarded-Proto: https`; store only the
   SHA-256 of the token, look up by hash, enforce `LAYWATCH_SESSION_TTL` (default 30 days) absolute
   expiry with sliding `last_seen`; rotate on login and on privilege change; password change revokes
   every other session for that user; `revoke_all` helper used by `DELETE /api/v1/sessions`.
3. `layawatch/auth/csrf.py`: issue a readable `lw_csrf` cookie at login and require it echoed in the
   `X-CSRF-Token` header on every mutating request from a browser session; missing or mismatched
   token is `403`. Key-auth and legacy admin-token mutations are exempt (`docs/api-reference.md` 1).
4. `layawatch/auth/users.py` plus `layawatch/api/users.py`: implement `GET/POST /api/v1/users`,
   `PATCH/DELETE /api/v1/users/{id}`, `GET /api/v1/users/{id}/sessions`, `DELETE /api/v1/sessions`
   per `docs/api-reference.md` section 10. Enforce in code: last enabled owner can never be deleted,
   demoted or disabled; a user cannot change their own role or delete themselves; role check failure
   returns `403` with an audit row `result = denied`; owner-created users get `must_change = 1` and
   are blocked from every endpoint except `POST /api/v1/auth/password` until the password is set.
5. `layawatch/api/auth.py`: `POST /api/v1/auth/login` (same response body for unknown email and
   wrong password, `401` on failure, never enumerate users), `POST /api/v1/auth/logout`,
   `GET /api/v1/auth/me` (user, role, permissions), `POST /api/v1/auth/password` (requires current
   password, revokes other sessions, clears `must_change`). First run: `POST /api/v1/auth/setup`
   creates the first owner on an empty database and closes permanently after; when
   `LAYWATCH_BOOTSTRAP_OWNER=email:password` is set on an empty database the owner is created at
   startup and the variable is ignored afterwards.
6. `layawatch/api/keys.py` plus hashing in `layawatch/engine/keys.py`: issue
   `lay_` + 32 base62 chars, store `HMAC-SHA256(pepper, secret)` with the pepper from
   `LAYA_STATE_DIR/secret.key` (0600, generated on first start) per `docs/security.md` 3.3; prefix
   (first 8 chars) lookup then `hmac.compare_digest`; plaintext returned once and never stored or
   logged; rotate keeps the old hash valid for a 5-minute grace window; revoke is a soft delete;
   `POST /api/v1/keys/auth` arms (requires at least one active key) or disarms, both audited;
   `PATCH /api/v1/keys/{id}` sets `rate_limit_per_min` and `burst` (null clears the override);
   `request_count` and `last_used` updated in the writer batch, not per request.
7. `layawatch/auth/ratelimit.py`: token bucket per subject keyed by id, capacity `burst` (default =
   limit), refill `limit / window` per second on `time.monotonic()`; FIFO fairness (rejected request
   consumes no token); 60 s sweeper that drops idle buckets so the map stays bounded; login failure
   counter with exponential backoff after the 5th failure (`2^(n-5)` seconds, capped at 15 minutes,
   reset on successful login or owner action); engine inflight semaphore sized
   `LAYWATCH_ENGINE_MAX_INFLIGHT` with bounded wait `LAYWATCH_ENGINE_QUEUE_MAX`, shedding excess
   with `429` `engine_saturated`; counters `ratelimit_blocks_total` and `engine_shed_total`.
8. `layawatch/api/ratelimits.py`: `GET /api/v1/ratelimits` (viewer+, effective policy plus per-key
   overrides), `POST /api/v1/ratelimits` (admin+, partial validated update, reads win over env
   defaults, applied on the next request with no restart), `GET /api/v1/ratelimits/usage` (viewer+,
   live bucket rows for the top 10 subjects), `POST /api/v1/ratelimits/reset` (admin+, one subject
   or all, audited `ratelimit.reset`). Policy rows live in `settings` under `ratelimit.*` keys with
   `updated_at` and `updated_by`.
9. `layawatch/http/middleware.py`: enforcement points from `docs/rate-limiting.md` 3. IP bucket
   before body read on engine endpoints (early `429`, `Content-Length: 0`); key bucket after auth
   with per-key override; session mutation bucket (60/min, owner and admin exempt) before mutation
   handlers; playground run bucket (30/min); loopback IP exemption unless
   `LAYWATCH_RATELIMIT_LOOPBACK=1`; attach `RateLimit-Limit`, `RateLimit-Remaining`,
   `RateLimit-Reset` on every engine response and `Retry-After` (ceiling-rounded seconds) on 429,
   with the error envelope codes `rate_limited` and `engine_saturated`.
10. 429 observability: record throttled and shed engine requests as traces with `status = 429`,
    `error_code` of `rate_limited` or `engine_saturated`, and a `rate_limit` observation carrying
    `scope`, `limit`, `remaining` (add the span to `layawatch/obs/vocabulary.py`); add the
    `throttled` rollup metric in `layawatch/obs/rollup.py`; expose both counters on
    `/api/v1/meta`.
11. `layawatch/auth/audit.py`: helper that writes an `audit_log` row inside the caller's
    transaction so an action cannot succeed without its record. Cover every action listed in
    `docs/api-reference.md` section 13 that this phase owns: `auth.login`, `auth.login_failed`,
    `auth.login_blocked`, `auth.logout`, `key.created`, `key.rotated`, `key.revoked`,
    `key.auth_changed`, `user.created`, `user.role_changed`, `user.disabled`, `user.deleted`,
    `session.revoked_all`, `ratelimit.updated`, `ratelimit.reset`. Login failures record email and
    ip, never the password. Denied role checks write `result = denied`.
12. Wire role gates into the mutation handlers that Phase 2 left open (trace delete, tags, scores,
    settings write, model load/unload, playground run) using one shared permission table; no handler
    checks roles ad hoc (`docs/security.md` section 2).
13. Tests: the eight acceptance criteria in `docs/rate-limiting.md` section 11, plus last-owner
    protection (delete, demote and disable all fail), CSRF rejection (mutation without `X-CSRF-Token`
    returns 403), session rotation on login and privilege change, API key plaintext absent from
    database, logs and API responses (grep test on a fixture secret), user-enumeration-safe login
    (unknown email and wrong password return an identical body), password policy, grace-window
    rotation (old and new secret both valid for 5 minutes, old rejected after), and sweeper
    boundedness (10,000 subjects then a sweep leaves the map bounded).

## Acceptance criteria

1. `docs/rate-limiting.md` 11.1: per-key limit set to 3/min, 5 requests yield 3 x 200 and 2 x 429
   with `Retry-After`, and both 429s appear as traces with `error_code = rate_limited`.
2. `docs/rate-limiting.md` 11.2: successful engine responses carry `RateLimit-Limit`,
   `RateLimit-Remaining` and `RateLimit-Reset` reflecting remaining tokens.
3. `docs/rate-limiting.md` 11.3: six wrong-password logins return `429` on the sixth with an
   increasing `Retry-After`; audit holds `auth.login_failed` for attempts 1 to 5 and
   `auth.login_blocked` for attempt 6.
4. `docs/rate-limiting.md` 11.4: exceeding `LAYWATCH_ENGINE_QUEUE_MAX` yields `engine_saturated`,
   distinguishable from `rate_limited` in traces and in the `throttled` rollup split.
5. `docs/rate-limiting.md` 11.5: `POST /api/v1/ratelimits` changes a limit, the next request uses
   it without a restart, and an audit row carries before and after values.
6. `docs/rate-limiting.md` 11.6: `POST /api/v1/ratelimits/reset` clears a bucket; an immediately
   following request that would have been throttled succeeds, and the reset is audited.
7. `docs/rate-limiting.md` 11.7: loopback exemption follows `LAYWATCH_RATELIMIT_LOOPBACK`, verified
   by test in both settings.
8. `docs/rate-limiting.md` 11.8: a unit test inserts 10,000 distinct subjects, runs the sweeper,
   and asserts the bucket map is bounded afterwards.
9. Last owner: deleting, demoting or disabling the only enabled owner returns `403` and writes an
   audit row with `result = denied`; a user cannot change their own role or delete themselves.
10. A mutation from a session without `X-CSRF-Token` (or with a mismatched token) returns `403`;
    the same mutation with the correct header succeeds.
11. A session id observed at login changes after a privilege change; password change revokes every
    other session for that user while the acting session stays valid.
12. After issuing a key, grepping `state.sqlite3`, the process log and every API response for the
    plaintext secret finds nothing; rotate keeps both secrets valid for 5 minutes and rejects the
    old one after.
13. Login with an unknown email and login with a known email plus wrong password return byte-identical
    `401` bodies; neither reveals whether the account exists.
14. `POST /api/v1/auth/setup` creates the first owner on an empty database and returns `403` on a
    database that already has a user; with `LAYWATCH_BOOTSTRAP_OWNER` set, first start creates the
    owner and a second start ignores the variable.
15. Every mutation listed in Task 11 writes its `audit_log` row in the same transaction as the
    change: a test kills the transaction mid-action and observes neither the action nor the row.

## Evidence required

- `make test` output showing the new test modules passing (password, session, csrf, user, ratelimit,
  login_ux) with pass counts.
- `curl` transcript for criteria 1 to 3: five engine requests showing headers, the 429 envelope and
  `Retry-After`, then six failed logins with audit rows dumped via
  `sqlite3 state.sqlite3 "SELECT action, result FROM audit_log ORDER BY id"`.
- `sqlite3` dump of a 429 trace with its `rate_limit` observation, plus the `throttled` rollup row.
- `grep -r <secret> state.sqlite3 log.txt` style check output showing no match for the key plaintext
  (criterion 12), alongside the API response showing the secret exactly once.
- Output of `POST /api/v1/ratelimits` before and after a limit change, the next request's
  `RateLimit-*` headers, and the matching `ratelimit.updated` audit row with before/after values.
- Sweeper unit test output asserting bounded map size after 10,000 subjects.
- Audit coverage query: `sqlite3 state.sqlite3 "SELECT DISTINCT action FROM audit_log"` compared
  against the action list in Task 11.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Login backoff locks out a legitimate operator behind one NAT | loopback exemption, backoff resets on successful login and owner action, `Retry-After` always present, audited `auth.login_blocked` so the operator can see why |
| Session or CSRF weakness mirrors the `docs/security.md` threat table | hash-at-rest lookup, HttpOnly plus SameSite=Lax, JSON-only content type, rotation on login and privilege change, rejection tests for every case |
| Grace window lets a revoked key keep working | window is fixed at 5 minutes, both hashes are dropped at window end, revoke path clears both immediately, tested |
| Runtime policy edit is read by some requests but not others | single `settings`-backed policy object read per request, no cached snapshot; criterion 5 asserts next-request effect |
| Sweeper or backoff grows memory without bound | sweeper drops idle buckets on a 60 s timer; criterion 8 is a bounded-map unit test |
| Audit written after the action can be lost | one transaction per action (Task 11), criterion 15 kills the transaction and observes nothing committed |
| Same-transaction audit conflicts with the writer-thread rule for traces | audit rows are small single inserts written directly by the mutation handler inside its own transaction; traces keep flowing through the writer queue |

## Rollback

Rate limiting is switchable at runtime (`settings.ratelimit_enabled`) and at first start with
`LAYWATCH_RATELIMIT_ENABLED=0`, which restores unthrottled behavior without a code change. Auth is
not optional once users exist, but `LAYA_ADMIN_TOKEN` remains accepted for mutations during v0.1, so
scripts keep working if sessions are disabled. Schema additions (if any migration is needed beyond
the Phase 0 schema) are additive table columns; reverting the phase is `git revert` plus keeping the
database, since `users`, `sessions`, `api_keys`, `audit_log` and `settings` already exist from
Phase 0.

## Exit gate

Per `plan/README.md` section 8: (1) all 15 acceptance criteria above verified by an observed
command or test run; (2) `plan/phase-3-auth-users/evidence.md` lists every command from Evidence
required with its observed output, including failures fixed along the way; (3)
`plan/phase-3-auth-users/issues.md` has no open `blocker` or `major`, deferred items labeled
`deferred`; (4) `plan/phase-3-auth-users/decisions.md` records the design choices made here (audit
transaction boundary, backoff curve source, settings-over-env precedence) with alternatives and
consequences; (5) `plan/phase-3-auth-users/outcome.md` states what shipped, what did not, and what
Phase 4 inherits (login endpoint contract for the UI); (6) `make test` passes and the phase smoke
path (login, create user, issue key, arm auth, hit the engine until 429, read audit) runs clean.
