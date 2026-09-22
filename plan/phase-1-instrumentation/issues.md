# Phase 1 issues

| ID | Severity | Summary | Status | Owner | Notes |
|---|---|---|---|---|---|
| P1-I1 | minor | smoke_laya.py's HTTP phase: the spawned server died silently mid-first-checkpoint-load on one run (log cut after `Fetching 5 files`, no traceback, client saw `RemoteDisconnected`) | resolved 2026-09-22 | - | self-inflicted: three checkpoint loads ran concurrently on a 15 GiB box (smoke parent + spawned server + a third evidence server). Solo rerun passed clean (smoke_exit=0). Lesson recorded: serialize engine-heavy evidence runs |
| P1-I2 | minor | a /predict body whose questions lack laya's required per-question `type` key surfaces as 500 `internal_error` (engine KeyError), not 400 | open | phase 2+ | deep engine schema errors are only detectable inside laya; legacy/serve.py behaves the same. If api-reference's `invalid_request` should cover it, the engine layer needs a typed schema rejection like `EnglishOnlyError` |
| P1-I3 | nit | two LayaWatch servers (or a smoke parent holding an engine plus a child server) roughly double checkpoint RAM: this box (15 GiB) got tight during parallel evidence runs | open | ops docs | documented behavior of lazy per-process loading; operations.md should note one engine per host for CPU deployments when Phase 8 lands |
