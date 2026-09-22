# LayaWatch documentation

| Document | Purpose | Audience |
|---|---|---|
| [design-language.md](design-language.md) | The UI/UX contract: tokens, depth law, layout system, component catalog with attributes and behavior, motion, accessibility, content rules, data-viz rules | frontend, design |
| [architecture.md](architecture.md) | Process model, module layout, request lifecycle, storage schema, config, build and deployment topologies, resource budgets | engineering |
| [observability-model.md](observability-model.md) | Trace / observation / score / rollup / log data model, span vocabulary, sampling, retention, redaction | engineering, operators |
| [api-reference.md](api-reference.md) | HTTP API surface: resources, filters, pagination, SSE stream, errors, legacy compatibility | frontend, integrators |
| [security.md](security.md) | Users, roles, sessions, password hashing, API keys, CSRF, audit log, threat model, hardening | operators, security |
| [rate-limiting.md](rate-limiting.md) | Managed rate limiting: policy model, enforcement points, response contract, live usage, management API and UI, acceptance criteria | operators, engineering |
| [performance.md](performance.md) | Budgets, measurement harness, optimization catalog with expected effects, regression guards | engineering |
| [operations.md](operations.md) | Install, run, configure, back up, restore, upgrade, monitor, troubleshoot, resource budgets | operators |

## Conventions

- **Single source of truth.** UI behavior is defined in `design-language.md`, not in tickets. If code
  and the design language disagree, one of them is a bug; record the resolution in the relevant
  `plan/phase-*/decisions.md`.
- **Versioned design language.** `design-language.md` carries a version header. Component changes
  that alter behavior or attributes bump it and are recorded as a decision.
- **Diagrams** are Mermaid blocks so they render on GitHub and in review.
- **No em-dashes, no emoji** in UI copy or documentation. Timestamps are ISO 8601 with an explicit
  zone. Units are spelled out in labels (`ms`, `MB`, `/s`).
- **Evidence over claims.** Every phase in `plan/` records commands and observed results in its
  `evidence.md`. A phase without evidence is not done.
