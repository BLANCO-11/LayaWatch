# LayaWatch

Self-hosted observability and operations console for the [laya](https://github.com/convaiinnovations) System 1 decision engine.

LayaWatch runs the laya engine in-process and turns every `/predict` and `/route` call into a
first-class trace: spans for language detection, routing, queue wait, model load, forward pass and
serialization, plus metrics, logs, scores, API keys, model management, a playground and an admin
surface with real user accounts.

Design goals, in order:

1. **Minimal footprint.** One Python process, three small runtime dependencies (FastAPI,
   uvicorn, httpx), SQLite on disk, no Node at runtime, no external services, no telemetry egress.
2. **Full control.** Everything is local: state, credentials, retention, backups. One directory to
   back up, one command to run.
3. **Real observability.** Trace-level detail (Langfuse-shaped model: traces, observations, scores)
   that answers "why was this request slow or rejected", not just "how many requests".

## Status

Phases 0-8 complete and gated: full pytest suite green, all nine performance budgets met,
the container contract tests pass and the image builds and boots hardened (non-root,
read-only, migrations applied). This tree is the v0.1.0 deliverable set.

- `layawatch/` Python package: app (FastAPI app factory), http, store, obs, auth, engine, api
- `web/` Next.js console, built to a static export and served by the Python process
- `docs/` product, design and operations documentation
- `plan/` phased implementation plan, one directory per phase with `plan.md`, `evidence.md`,
  `issues.md`, `decisions.md`, `outcome.md`
- `tests/` pytest suite (`make test`), `scripts/` dev, smoke, budget and operator helpers

## Quickstart

The app is a Docker instance first; source and systemd are alternatives.
`docs/operations.md` section 2 is the long form.

**Container (recommended):**

```bash
docker compose up -d                              # or: make up — ./Dockerfile
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8050/healthz   # 200
```

State and the model cache live in named volumes (`layawatch-data`, `layawatch-models`);
the image itself ships no state, no secrets and no Node.

**Source (development):**

```bash
git clone https://github.com/BLANCO-11/LayaWatch.git && cd LayaWatch
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pip install fastapi uvicorn httpx
cd web && npm ci && npm run build && cd ..        # produces web/out
python -m layawatch                               # serves http://127.0.0.1:8050
```

Local knobs (owner bootstrap, port, bind) live in `.env` at the repo root — copy
`.env.example` to `.env` and edit; it is loaded at boot, never committed and never
baked into the image, and real environment variables win.

**systemd:** install `layawatch.service` per `docs/operations.md` section 2.3
(`sudo cp layawatch.service /etc/systemd/system/ && sudo systemctl daemon-reload &&
sudo systemctl enable --now layawatch`).

First run on any path: open the console, create the first owner in the setup wizard (or set
`LAYWATCH_BOOTSTRAP_OWNER`), create an API key, arm key auth, confirm `/healthz` reports
`auth_enabled: true` (`docs/operations.md` section 3). Backup, restore and upgrade are
`scripts/backup.sh`, `scripts/restore.sh` and `scripts/upgrade.sh`.

## Repository map

```
layawatch/        Python package: app (FastAPI app factory), http, store, obs, auth, engine, api
web/              Next.js app, built to a static export and served by the Python process
web/design/       approved mock + design references
Dockerfile        multi-stage image: Node builds web/out, python:3.12-slim serves (non-root); WITH_ENGINE build arg (default 1) ships engine wheels, 0 = console-only
docker-compose.yml single-service compose with healthcheck and hardened defaults
layawatch.service hardened systemd unit (docs/operations.md section 2.3)
docs/             design language, architecture, observability model, API, security, operations
plan/             master plan + per-phase plan/evidence/issues/decisions/outcome files
tests/            pytest suite
scripts/          dev, smoke, bench, budget, backup/restore/upgrade and e2e helpers
```

## Documentation

Start with [`plan/README.md`](plan/README.md) (master plan) then
[`docs/design-language.md`](docs/design-language.md) (UI/UX contract) and
[`docs/architecture.md`](docs/architecture.md) (system design).

## License

Not yet chosen. See open questions in the master plan.
