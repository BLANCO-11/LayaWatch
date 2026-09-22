# Phase 6 - Operate and admin views

Status: planned
Depends on: phase 5
Estimated effort: 6 to 8 days
Deliverable: all six operate and admin views run against the live API: playground runs with scores,
checkpoint load and unload, key and user management, audit filtering, and settings for limits,
retention, capture and diagnostics, all under role-aware controls with toasts and audit entries.

## Objective

Build the write half of the console: the Playground, Checkpoints, API Keys, Users, Audit and Settings
routes from `docs/design-language.md` section 3, wired to the Phase 2 and Phase 3 endpoints, reusing
the Phase 4 component catalog, so an operator can run the engine, manage credentials and accounts, and
change runtime policy entirely from the browser with every mutation surfaced as a toast and recorded
in the audit log.

## Scope

In: six routes under `web/src/app/`; a shared role and permission layer driven by
`GET /api/v1/auth/me`; a shared mutation helper (toast on success and failure, then audit-visible
refetch); Playground with templates, model select, state editor, answer rendering and score
submission; Checkpoints with live model state, confirm dialogs and the `LAYA_ENGLISH_ONLY=1`
explanation; API keys with create, rotate, revoke, per-key limits and the arm switch; Users with role
badges, sessions and last-owner protection surfaced in the UI; a filterable Audit view; the Settings
page with the rate limits card (every field from `docs/rate-limiting.md` 6.1), the live bucket
panel, retention and sampling with a disk estimate, owner-only payload capture, diagnostics, legacy
shim counters and a danger zone; two additive API extensions (D-013, D-014) and the `source` field
that labels playground traces in the Traces list; contract tests and Playwright specs; doc updates in
the same change.

Out: auth, role enforcement and rate limit enforcement logic (Phase 3, consumed here only); the
Overview, Traces, Metrics and Logs views (Phase 5, except the one shared playground chip); app
shell, tokens and the shared component catalog (Phase 4); performance work (Phase 7); packaging
(Phase 8); annotation platform features such as queues, datasets or LLM judging (non-goal, scores
stay the two playground fields of `docs/observability-model.md` 8); new sidebar nav groups
(Observe, Operate and Admin are fixed per `docs/design-language.md` 3).

Decisions proposed: D-013 `DELETE /api/v1/traces?since=&until=` for the danger zone bulk delete
(the only documented delete is per trace, and a client-side loop would exhaust the 60/min session
mutation limit); D-014 a per-model `stats` block on `GET /api/v1/models` (`size_bytes`, `load_ms`,
`requests_24h`, `p50_ms`) because no existing endpoint exposes checkpoint size or per-model load
time. Both are additive, recorded in `decisions.md`, and documented in `docs/api-reference.md` in
the same change.

## Deliverables

```
layawatch/api/traces.py        source field on the summary item; D-013 bulk range delete (admin+, audited)
layawatch/api/models.py        D-014 per-model stats block on GET /api/v1/models
web/src/lib/permissions.ts     role and permission map from /api/v1/auth/me, can(action) helper
web/src/lib/mutation.ts        shared mutate helper: CSRF header, toast on success and failure, refetch
web/src/app/playground/page.tsx      Playground route
web/src/app/checkpoints/page.tsx     Checkpoints route
web/src/app/keys/page.tsx            API Keys route
web/src/app/users/page.tsx           Users route
web/src/app/settings/page.tsx        Settings route (cards below)
web/src/app/audit/page.tsx           Audit route, linked from Settings; no new nav group
web/src/components/ui/DisabledReason.tsx   disabled control wrapper with tooltip reason
web/src/components/playground/TemplateSelect.tsx  built-in templates with their questions
web/src/components/playground/StateEditor.tsx     mono textarea state and question editor
web/src/components/playground/AnswerPanel.tsx     choice bars, noul probability, status and latency badge
web/src/components/playground/ScorePanel.tsx      helpful and quality submission to /api/v1/traces/{id}/scores
web/src/components/checkpoints/ModelTable.tsx     loaded and available chips, size, load time, requests, p50
web/src/components/checkpoints/ModelActions.tsx   load and unload confirm dialogs, RAM guard and english-only feedback
web/src/components/keys/KeyTable.tsx              prefix, status, requests, last used, Limit column
web/src/components/keys/KeyDialog.tsx             create and rotate flows with SecretReveal
web/src/components/keys/LimitDialog.tsx           per-key limit and burst, reset usage action
web/src/components/keys/AuthSwitch.tsx            arm and disarm key auth switch
web/src/components/users/UserTable.tsx            rows with role badges and disabled protected actions
web/src/components/users/UserDialog.tsx           create user, role change, disable, force password change
web/src/components/users/SessionDrawer.tsx        per-user session list, revoke all sessions
web/src/components/audit/AuditTable.tsx           audit row component with actor, action, range filters
web/src/components/settings/RateLimitsCard.tsx    all nine controls from docs/rate-limiting.md 6.1
web/src/components/settings/BucketPanel.tsx       live bucket usage with per-row reset
web/src/components/settings/RetentionCard.tsx     retention, sampling, stream tick with disk estimate
web/src/components/settings/CaptureCard.tsx       payload capture toggle, owner only, privacy warning
web/src/components/settings/DiagnosticsCard.tsx   self-observability counters from /api/v1/meta
web/src/components/settings/ShimCountersCard.tsx  legacy shim usage counters from /api/v1/meta
web/src/components/settings/DangerZoneCard.tsx    delete trace range, revoke all sessions
tests/                               contract tests for D-013, D-014, source field, stats accuracy
tests/ui/                             Playwright specs (harness from Phase 4) for the six views and viewer role
docs/api-reference.md                 sections 5, 11 and a new bulk delete row, updated with the code
```

## Tasks

1. `web/src/lib/permissions.ts`: map the role and permissions from `GET /api/v1/auth/me` to a
   `can(action)` predicate covering `playground.run`, `model.load`, `key.create`, `key.rotate`,
   `key.revoke`, `key.auth`, `key.limit`, `user.write`, `session.revoke`, `settings.write`,
   `capture.toggle`, `trace.delete_range` following the matrix in `docs/security.md` section 2.
   Wrap disabled controls in `web/src/components/ui/DisabledReason.tsx`, which renders the real
   `disabled` attribute plus a Tooltip (design 4.23) naming the reason (`Viewer role is read-only`,
   `Only the owner can change payload capture`, `The last owner cannot be demoted`).
2. `web/src/lib/mutation.ts`: one helper that attaches the `X-CSRF-Token` header, calls the API,
   raises a Toast (design 4.21, errors persist until dismissed, success auto-dismisses at 6 s),
   renders the error envelope (`code` plus `message`) on failure, and triggers a refetch of the
   affected query. Every task below routes mutations through it; no ad hoc `fetch` in components.
3. Playground route (`web/src/app/playground/page.tsx`): TemplateSelect fetches
   `GET /api/v1/playground/templates` (department, urgency, churn) and fills the question list;
   a ModelSelect is populated from `GET /api/v1/models`; StateEditor is the mono textarea
   (design 4.3) for the state plus an editable question list. Run posts
   `POST /api/v1/playground/run` with `{state, questions, model, task, lang, temperature}` through
   the mutation helper. A 429 surfaces the playground-specific reason (`30 runs per minute`) and the
   `Retry-After` value; the Run button takes a loading state with locked width (design 4.1).
4. Playground answers (`AnswerPanel.tsx`): render each answer as choice bars, one bar per choice with
   its probability drawn to the token chart rules, plus the `noul` probability line the engine
   returns (rendered only when the field is present). A status badge shows the HTTP outcome
   (`200 OK`, `422 reject`, `429 rate limited`) and a mono badge shows measured latency in ms;
   the badge links to the trace detail route via the `X-Request-Id` response header.
5. Score submission (`ScorePanel.tsx`): a `helpful` checkbox and a 1 to 5 `quality` control post to
   `POST /api/v1/traces/{id}/scores` with `source: "playground"` through the mutation helper; the
   submitted scores appear in the trace detail timeline (design 4.17) on refetch. Exactly these two
   scores exist; no annotation controls (non-goal, `plan/README.md` section 3).
6. Playground labeling: add `source` (taken from `meta.source`) to the trace summary item in
   `layawatch/api/traces.py` and update the example in `docs/api-reference.md` section 5; render a
   `playground` chip in the Traces table page `web/src/app/traces/page.tsx` (one shared column with
   Phase 5, coordinate before editing). Playground runs therefore appear labeled in the Traces list
   within one stream tick.
7. Checkpoints route (`web/src/app/checkpoints/page.tsx`): chips and rows from `GET /api/v1/models`
   with `loaded` and `available` badges (design 4.6). The per-model table columns come from the
   D-014 stats block: size (`size_bytes`), last load time (`load_ms` from the most recent
   `model.load` span), requests (`requests_24h`) and `p50` (`p50_ms` from `metric_rollup`),
   right-aligned and tabular per design 4.11. Footer shows `device` and `rss_mb`.
8. Model actions (`ModelActions.tsx`): Load and Unload open a confirm dialog (design 4.19) that
   names the checkpoint; while a request is in flight both buttons are disabled (single in-flight
   guard, `409` from the API is still handled). Success or failure shows a toast; a RAM guard
   refusal displays the server reason with the available and required MB; when
   `LAYA_ENGLISH_ONLY=1` blocks multilingual, the multilingual load control is disabled with a
   Tooltip reason and a warn Banner (design 4.22) states `Multilingual cannot be loaded while
   LAYA_ENGLISH_ONLY=1`, matching the server `409` body. Subscribing to the SSE `model` event
   refetches `GET /api/v1/models`, so loads and unloads done by other clients or scripts appear
   live.
9. API keys route (`web/src/app/keys/page.tsx`): KeyTable columns prefix, status badge
   (`active` or `revoked`), request count, last used and the `Limit` column (mono, `unlimited` or
   `600/min`, from `docs/rate-limiting.md` 6.2). Create and rotate open KeyDialog; on success the
   dialog shows the plaintext once inside the SecretReveal component (design 4.28: dashed border,
   `shown once, store it now`, Copy button) and never inside a toast. Revoke requires a confirm
   dialog naming the key (`Revoke lay_3f8a...`) and keeps the row as `revoked`. LimitDialog
   (limit, burst, `Reset usage`) PATCHes `/api/v1/keys/{id}`; reset posts
   `/api/v1/ratelimits/reset` and is audited `ratelimit.reset`. AuthSwitch posts
   `POST /api/v1/keys/auth`; arming is disabled with a reason when no active key exists; both
   directions toast and write `key.auth_changed`.
10. Users route (`web/src/app/users/page.tsx`): UserTable rows carry an avatar plus a role badge
    (`owner`, `admin`, `viewer`, design 4.30 and 4.6). UserDialog covers create
    (`POST /api/v1/users`, owner only), role change, disable and force password change
    (`PATCH /api/v1/users/{id}`); each runs through the mutation helper and toasts. SessionDrawer
    lists `GET /api/v1/users/{id}/sessions` (created, last seen, user agent, ip) with a
    `Revoke all sessions` action (`DELETE /api/v1/sessions`, confirm dialog). Last-owner and
    self-protection (invariants 1 and 2 in `docs/security.md` section 2) are surfaced as disabled
    demote, disable and delete controls with a tooltip reason naming the invariant; the server 403
    remains authoritative and its message is shown if a race slips through. Admins get a read-only
    table (owner-only actions disabled with reason); viewers never reach the route (nav item hidden,
    direct URL shows the access-denied state from Phase 4).
11. Audit route (`web/src/app/audit/page.tsx`): a filter bar (design 4.13) with actor text filter
    (250 ms debounce), action select populated from the action list in `docs/api-reference.md`
    section 13, and a range segmented control; filters live in the URL query string. Rows use the
    AuditRow component (design 4.31): mono timestamp, actor with avatar, action verb, target, result
    badge. Cursor pagination via the `Load older` button (design 4.12). Read-only, admin+ only;
    linked from the Settings page footer, with no new sidebar nav group.
12. Settings, rate limits (`RateLimitsCard.tsx`): render all nine controls of
    `docs/rate-limiting.md` 6.1: enable switch, engine per key (0 shows `unlimited`), engine per IP,
    login attempts with a window select (`5m`, `15m`, `1h` mapped to 300, 900, 3600 seconds),
    mutation per session, playground runs, engine inflight cap, engine queue bound, exempt loopback
    switch. Load from `GET /api/v1/ratelimits`; each control carries its origin marker (`from env`
    locked or `editable`) per section 8. Save posts `POST /api/v1/ratelimits` through the mutation
    helper; the success toast states that the value applies on the next request with no restart.
13. Settings, live buckets (`BucketPanel.tsx`): flat table of `subject`, `scope`, `used / limit`,
    `resets in` from `GET /api/v1/ratelimits/usage`, only rows with usage above 0, sorted by usage
    ratio (section 6.4), refreshed on every SSE `pulse`. Per-row `Reset` button (admin+) posts
    `/api/v1/ratelimits/reset` and toasts; an empty panel shows `No buckets in use`.
14. Settings, retention and capture: RetentionCard edits trace cap, age days, log cap, trace sample
    fraction and stream tick via `POST /api/v1/settings`; before saving it displays a disk estimate
    computed from the growth table in `docs/observability-model.md` section 10 (steady-state bytes
    per trace times the proposed cap), labeled as an estimate with the formula in the section meta.
    CaptureCard holds the payload capture switch: disabled with a Tooltip reason for anyone below
    owner, showing a warn Banner privacy warning (capture stores truncated state and answer
    payloads in SQLite, off by default per D-005) before the first enable, and writing
    `payload_capture.toggled` on change.
15. Settings, diagnostics and shims: DiagnosticsCard renders the self-observability counters from
    `GET /api/v1/meta` as a key-value list (design 4.27): `obs_dropped_total`, `write_queue_depth`,
    `write_latency_ms`, `db_size_bytes`, `rss_mb`, `ring_usage`, `sse_clients`,
    `ratelimit_blocks_total`, `engine_shed_total`, refreshed on SSE `pulse`. ShimCountersCard shows
    the per-endpoint legacy shim counters from the same payload with a note that they tell the
    operator whether anything still depends on the shims before they are removed (D-006).
16. Settings, danger zone (`DangerZoneCard.tsx`): a flat danger-variant card with two actions, each
    behind a confirm dialog that names the object and the consequence (design 4.19). Delete trace
    range opens a since/until picker, shows the matching trace count preview from
    `GET /api/v1/traces?since=&until=&limit=1` (`total_estimate`), and calls the D-013 bulk delete;
    the audit `trace.deleted` meta carries the range and count. Revoke all sessions calls
    `DELETE /api/v1/sessions`. Both actions are disabled with a Tooltip reason below admin.
17. Backend additions: implement D-013 and D-014 in `layawatch/api/traces.py` and
    `layawatch/api/models.py`; both endpoints use the shared permission table and error envelope
    from Phase 3, write audit rows in the same transaction (D-013), and update
    `docs/api-reference.md` in the same change (a stale doc is a defect).
18. Tests: contract tests for the source field, bulk range delete (exact range removed, count in
    audit, 403 for viewer and admin-gated for owner rules), and stats block accuracy against a
    seeded `metric_rollup`; Playwright specs in `tests/ui/` covering each of the six views, the
    viewer read-only pass (every mutation control disabled with a tooltip), the secret reveal flow,
    the rate limit change followed by a request, and the english-only banner with a 409 fixture.

## Acceptance criteria

1. Playground: selecting a template fills the questions, Run posts to
   `POST /api/v1/playground/run`, answers render as choice bars with probabilities plus the `noul`
   probability line, a status badge shows `200 OK` and a latency badge shows the measured ms; within
   one stream tick the Traces list shows the run with a `playground` chip.
2. Submitting `helpful` and `quality` from the playground stores two score rows retrievable from
   `GET /api/v1/traces/{id}` with `source = "playground"`, and they appear in the trace detail
   timeline after refetch.
3. Checkpoints: load and unload behind a confirm dialog update the chips and table within one SSE
   `model` event; a load refused by the RAM guard shows a toast with the available and required MB;
   with `LAYA_ENGLISH_ONLY=1` the multilingual action is disabled with a tooltip reason and the warn
   banner text, and the server `409` body matches what the UI shows; a load click while another load
   is in flight is disabled.
4. Keys: create and rotate reveal the plaintext only inside SecretReveal with the
   `shown once, store it now` note, and grepping `state.sqlite3`, the process log and every later API
   response for that plaintext finds nothing; revoke requires a confirm dialog naming the prefix and
   flips the status badge to `revoked`; arming with zero active keys is blocked with a reason; the
   `Limit` column shows the value written by `PATCH /api/v1/keys/{id}`.
5. Users: create, role change, disable and force password change each refresh the row and write the
   matching audit action (`user.created`, `user.role_changed`, `user.disabled`); the session drawer
   lists one user's sessions and revoke all empties it; on the last enabled owner the demote, disable
   and delete controls are disabled with a tooltip reason, and the same operation called directly
   against `PATCH /api/v1/users/{id}` still returns `403`.
6. Audit: filtering by actor, action and range returns only matching rows in the AuditRow layout
   with working `Load older` pagination; after performing the mutations in criteria 3, 4, 5 and 7,
   `sqlite3 state.sqlite3 "SELECT action, result FROM audit_log"` contains one `ok` row per mutation.
7. A rate limit changed from the UI takes effect on the next request: after saving Engine per key in
   Settings, the very next engine response carries the new `RateLimit-Limit` header without a
   process restart, and the audit log holds `ratelimit.updated` with before and after values.
8. The viewer role sees no enabled mutation controls: on all six views every create, edit, delete,
   load, revoke and save control is disabled with a tooltip reason, and the same mutation sent
   directly with a viewer session returns `403`.
9. Saving retention displays the disk estimate computed from `docs/observability-model.md` section 10
   before the write; the payload capture toggle is disabled with a reason for an admin and enabled
   for the owner behind the privacy warning, and toggling writes `payload_capture.toggled`.
10. The diagnostics counters and the live bucket panel refresh on the SSE `pulse`; calling a legacy
    shim endpoint increments the counter displayed in Settings; a per-row bucket reset clears that
    row from the panel and writes an audit row `ratelimit.reset`.
11. The danger zone deletes exactly the traces in the chosen range (the audit `trace.deleted` meta
    count equals the deleted row count) behind a confirm dialog, and revoke all sessions ends every
    session except the current one; both actions are disabled for viewers.
12. `make web-build` produces `web/out`, the Python process serves the six routes as static HTML
    with `no-cache` on documents, and no Node process runs at runtime.

## Evidence required

- `make test` output listing the new contract tests (source field, bulk delete, stats) with pass
  counts, and the Playwright run for `tests/ui/` with its result summary.
- `curl` transcript: `POST /api/v1/ratelimits` changing Engine per key, then the next `POST /predict`
  response headers showing the new `RateLimit-Limit`, plus the `sqlite3` audit row with before and
  after values.
- `curl -N localhost:8050/api/v1/stream` capture showing `model` events while loading and unloading
  a checkpoint from the UI.
- `sqlite3 state.sqlite3 "SELECT action, result FROM audit_log ORDER BY id"` after a scripted UI pass
  through playground, keys, users, settings and danger zone actions.
- `grep -r <secret> state.sqlite3 log.txt` style output showing no match for a freshly revealed key
  plaintext after the dialog is closed.
- Screenshots of all six views in dark and light themes, plus one viewer-role session screenshot
  showing disabled controls with a tooltip open.
- `make web-build` output and `curl -I localhost:8050/settings` showing HTML `no-cache`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bulk range delete removes too much or blocks the writer | count preview in the confirm dialog from `total_estimate`, one bounded transaction, admin+ only, audit meta carries range and count; the single-trace delete from Phase 5 remains as the fallback path |
| Client-side role gating treated as the security boundary | the UI layer is UX only; Phase 3 server checks stay authoritative, criterion 8 calls the API directly with a viewer session and expects 403 |
| Key plaintext leaks through toasts, history or the DOM | secrets appear only in SecretReveal, never in a toast (design 4.21 rule); grep test over database, log and responses, plus a DOM check after dialog close |
| Playground scoring grows into an annotation platform | exactly two score fields (`helpful`, `quality`); any queue, dataset or bulk labeling request goes to the phase outcome backlog, not into this phase (R-08) |
| Last-owner and self-protection logic duplicated in the client drifts from the server | the client only pre-disables obvious cases and always renders the server's 403 reason; a fixture test keeps the disabled reason aligned with the invariant text |
| D-014 stats queries slow down the Checkpoints page | one rollup query per model inside a single request, bounded by the same 50 ms p95 budget checked in Phase 2; computed server side, never per render |
| SSE-driven tables flicker or go stale | refetch the whole resource on `model` and `pulse` events instead of patching rows; skeletons only on first load (design 4.25) |
| Disk estimate misleads the operator | formula and source stated in the section meta, result labeled an estimate, both caps (rows and days) shown before saving |
| The english-only explanation disagrees with the server | one shared banner string tested against a recorded `409` fixture; the disabled tooltip and the banner are the only copy |

## Rollback

No schema migration is introduced, so rollback is `git revert` of the phase commits: the six static
routes disappear from the bundle and all server behavior returns to the Phase 5 state. The D-013 and
D-014 endpoints are additive and can be removed without touching earlier phases; until they exist,
danger-zone range deletion falls back to the per-trace `DELETE /api/v1/traces/{id}` and the
Checkpoints table drops its four stat columns. Settings values written from the UI live in the
existing `settings` table and remain valid for API and env-driven operation.

## Exit gate

Per `plan/README.md` section 8: (1) all 12 acceptance criteria verified by an observed command or a
Playwright check; (2) `plan/phase-6-operate-admin/evidence.md` lists every command from Evidence
required with its observed output, including failures fixed along the way; (3)
`plan/phase-6-operate-admin/issues.md` has no open `blocker` or `major`, deferred items labeled
`deferred`; (4) `plan/phase-6-operate-admin/decisions.md` records D-013, D-014, the audit route
placement (linked from Settings, no new nav group) and the disk estimate formula, with alternatives
and consequences; (5) `plan/phase-6-operate-admin/outcome.md` states what shipped, what did not, and
what Phase 7 inherits; (6) `make test` and `make web-build` pass and the phase smoke path (log in,
run the playground, score the trace, load and unload a checkpoint, create and rotate a key, create a
user, change a rate limit and observe it on the next request, read the audit log) runs clean.
