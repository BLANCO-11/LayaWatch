# LayaWatch

Self-hosted observability and operations console for the [laya](https://github.com/convaiinnovations) System 1 decision engine.

LayaWatch runs the laya engine in-process and turns every `/predict` and `/route` call into a
first-class trace: spans for language detection, routing, queue wait, model load, forward pass and
serialization, plus metrics, logs, scores, API keys, model management, a playground and an admin
surface with real user accounts.

Design goals, in order:

1. **Minimal footprint.** One Python process, zero added runtime dependencies (stdlib only), SQLite
   on disk, no Node at runtime, no external services, no telemetry egress.
2. **Full control.** Everything is local: state, credentials, retention, backups. One directory to
   back up, one command to run.
3. **Real observability.** Trace-level detail (Langfuse-shaped model: traces, observations, scores)
   that answers "why was this request slow or rejected", not just "how many requests".

## Status

Planning. Nothing here is implemented yet. The deliverable set is:

- `docs/` product, design and operations documentation
- `plan/` phased implementation plan, one directory per phase with `plan.md`, `evidence.md`,
  `issues.md`, `decisions.md`, `outcome.md`
- `web/design/mock.html` the approved UI/UX vision mock (design source of truth)

## Repository map (target)

```
layawatch/        Python package, stdlib only: http, store, obs, auth, engine, api
web/              Next.js app, built to a static export and served by the Python process
web/design/       approved mock + design references
deploy/           Dockerfile, compose.yaml, caddy, systemd units
docs/             design language, architecture, observability model, API, security, operations
plan/             master plan + per-phase plan/evidence/issues/decisions/outcome files
tests/            pytest suite
scripts/          dev, smoke and release helpers
```

## Documentation

Start with [`plan/README.md`](plan/README.md) (master plan) then
[`docs/design-language.md`](docs/design-language.md) (UI/UX contract) and
[`docs/architecture.md`](docs/architecture.md) (system design).

## License

Not yet chosen. See open questions in the master plan.
