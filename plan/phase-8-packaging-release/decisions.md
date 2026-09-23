# Phase 8 - Packaging, self-host and release: decisions

Status: in progress
Decision ids are global (D-0NN) and never reused. Each entry records context, alternatives and
consequences so a later reader does not have to reconstruct the reasoning.

## D-015: legacy removal sequencing (task 9)

- Date: 2026-09-23
- Status: accepted
- Context: D-006 retires the pre-LayaWatch surface at Phase 8. The surface spans the `legacy/`
  snapshot, `layawatch/api/legacy.py` (the `/admin` + `/admin/api/*` shims), the deprecation
  counters in the `/api/v1/meta` payload and the Settings `ShimCountersCard`, the
  `LAYA_ADMIN_TOKEN` credential in `config.py` and `auth/sessions.py`, and the docs that
  described them.
- Decision: remove all of it in one cutover, in dependency order: delete `legacy/`, the shim
  module and its tests; drop `config.admin_token` and the `authenticate`/`gate` credential
  branch (which also made `gate`'s `config` parameter dead - removed across all 25 call sites
  plus the registration-only `config` params in audit/keys/users/playground/range-delete);
  strip `deprecation` from the meta payload; delete the Settings card; make
  `http/static.resolve` answer the standard `404 not_found` envelope for `/admin` and
  `/admin/api/*` so a real server (static catch-all mounted) behaves like the router does;
  rewrite api-reference 1/14, security 3.4, architecture 6, observability-model's
  `auth.verify` scheme enum, operations and README in the same change.
- Alternatives considered: (a) keep `gate(config=...)` to avoid the call-site churn - rejected:
  the parameter existed only for the token and would be dead weight; (b) let old `/admin` paths
  fall through to the HTML console 404 page - rejected: acceptance 9 requires the JSON envelope.
- Consequences: `X-Admin-Token` authenticates nothing (mutation attempts get 401
  `missing_or_invalid_credential`); `/api/v1/meta` has no `deprecation` key; callers of the
  registration functions lost one keyword argument; `Principal.kind` only ever is `session` or
  `api_key`.
- Evidence / links: `tests/test_admin_removed.py` (envelope, 401, env-ignored, meta),
  grep sweep: `LAYA_ADMIN_TOKEN|/admin/api|ShimCounters|deprecation_counts|admin_token`
  outside the plan tree hits only the removal statement in api-reference 14, the new tests and
  `static.py`'s `_ADMIN_REMOVED` constants.

## D-016: api_keys.json compatibility window (task 10)

- Date: 2026-09-23
- Status: accepted
- Context: operators upgrading from the pre-LayaWatch host have keys in a raw `api_keys.json`.
  D-006 retires legacy surfaces, but deleting the importer in the same release would silently
  drop those keys.
- Decision: `v0.1.0` is the one release of compatibility. `layawatch/engine/legacy_state.py`
  stays in the tagged tree; when the import fires it emits a `DeprecationWarning` plus a log
  line stating the window; `scripts/upgrade.sh` preflight refuses to run while a raw
  `api_keys.json` (not `.imported`) exists in `LAYA_STATE_DIR`, printing the exact migrate
  command; api-reference 14 records that the import ends after `v0.1.0`. The module and its
  test are deleted in the first commit of the next cycle.
- Alternatives considered: (a) delete the importer now - rejected: key loss on upgrade;
  (b) keep it silently - rejected: no operator would know the path is dying.
- Consequences: one release carries the importer with a warning; the upgrade path is guarded
  by the preflight rather than by documentation alone.
- Evidence / links: `tests/test_legacy_state.py::test_import_emits_a_deprecation_warning`,
  upgrade preflight run (refusal output recorded in evidence.md), api-reference 14 text.

## D-NN: title

- Date:
- Status: proposed | accepted | superseded by D-0NN
- Context:
- Decision:
- Alternatives considered:
- Consequences:
- Evidence / links:
