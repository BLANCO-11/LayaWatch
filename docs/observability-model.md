# LayaWatch observability model

Version: 0.1 (2026-09-22)
Shape: Langfuse-like (trace, observation, score) trimmed to what a single-process engine can produce.

## 1. Entities

| Entity | Meaning | Cardinality |
|---|---|---|
| **trace** | one client request to `/predict` or `/route`, from socket accept to response write | one per request |
| **observation** | a timed unit of work inside a trace (span, generation, or event) | 6 to 12 per trace |
| **score** | an attached judgement about a trace (playground rating, API feedback) | 0..n |
| **rollup** | pre-aggregated metric buckets derived from traces | many per window |
| **log entry** | a structured log line, optionally linked to a trace | 0..n |

## 2. Span vocabulary

Canonical names, in expected order. Names are stable identifiers; the UI orders by `start_ms` and
groups by name. Every span records `start_ms` (offset from trace start) and `duration_ms`.

| Span | Type | Attributes | Notes |
|---|---|---|---|
| `http.receive` | span | `method`, `path`, `remote`, `content_length` | opens the trace |
| `auth.verify` | span | `scheme` (`api_key`, `session`, `none`), `key_id`, `ok` | 401/403 on failure |
| `body.parse` | span | `state_bytes`, `question_count`, `model_param`, `lang_param` | 400 on malformed JSON |
| `lang.detect` | span | `lang`, `confidence` | engine `analyse()` |
| `route.decide` | span | `model`, `reason`, `typed_workflow` | engine `Router.route()` |
| `queue.wait` | span | `depth_at_acquire` | time blocked on `_predict_lock`; the honest explanation for tail latency |
| `model.load` | span | `model`, `from` (`preloaded`, `disk`, `hub`), `bytes` | only when a checkpoint loads |
| `forward` | generation | `model`, `questions`, `temperature`, `device` | the torch pass; `type=generation` marks it as the model call |
| `serialize` | span | `answers`, `bytes` | response construction |
| `response.send` | span | `status`, `bytes` | closes the trace |
| `error` | event | `code`, `message`, `where` | added on failure, always recorded |
| `payload` | event | none | `/predict` request as `input`, response as `output`; recorded only while payload capture is on |

Two spans are **absent by design** when the client pins the decision they record (phase 7
section 4.4 item 21): `lang.detect` is skipped when the request pins `lang=` - the trace's
`lang` carries the pinned value instead, except on an english-only deployment where
detection still runs and records, because it gates the 422 refusal - and `route.decide` is
skipped when the request pins `model=`: the pin is the decision, `route_reason` keeps the
router's explicit-model reason, and the decision is computed once, untimed. `POST /route`
accepts no pins, so both spans are unconditional there.

Rejected requests produce a short trace: `http.receive`, `auth.verify`, `body.parse` or `lang.detect`,
`error`, `response.send`. A 422 non-English rejection is a successful observation of a refusal, not an
engine error.

## 3. Trace attributes

| Field | Source | Notes |
|---|---|---|
| `id` | generated | 8 hex chars, also returned as `X-Request-Id` |
| `route`, `method`, `status` | middleware | |
| `model`, `route_reason`, `lang` | engine | null for refusals |
| `queue_ms`, `forward_ms` | spans | surfaced separately in the table and waterfall |
| `state_bytes`, `question_count` | body parse | sizes only; content is never stored unless capture is enabled |
| `client_key_id` | auth | links a trace to an API key; the secret is never stored |
| `session_id` | optional | set when a request carries a session (playground runs) |
| `error_code`, `error_message` | error event | message truncated to 500 chars |
| `tags` | API or playground | free-form labels, max 10, each <= 32 chars |
| `meta` | server | `device`, `english_only`, `sampled`, `payload_capture`, `schema_version` |

## 4. Capture policy (privacy)

- Default: **no payload content**. State and answer bodies are represented by byte sizes, question
  counts and answer keys only.
- `LAYWATCH_CAPTURE_PAYLOADS=1` enables storage of truncated payloads (`LAYWATCH_PAYLOAD_MAX`, default
  2048 bytes) for the playground and for traces explicitly tagged `capture`.
- Redaction runs before truncation on keys matching `password`, `token`, `secret`, `api_key`,
  `authorization`, `email`, `phone`, `ssn`, `card` (case-insensitive, nested objects included).
- With capture on, every `/predict` records a `payload` event: the request (`state`, `questions`,
  and any `model`/`task`/`lang`) as `input` and the response as `output`, including for a refused
  request. The trace detail page renders both.
- Value-level scrubbing runs with the key redaction on captured payloads: email addresses,
  runs of 10+ digits (phone, card, account numbers) and credential-shaped tokens are replaced
  in every string. Numbers, booleans and nulls under a redacted key are kept (option names
  such as `customer_email` key probabilities). Names and addresses in prose are not detected.
- Capture is recorded per trace in `meta.payload_capture` so the UI can label it, and the trace detail
  page shows a warning banner when payloads are present.
- Retention for captured payloads is 24 h regardless of trace retention.

## 5. Ingest pipeline

```
handler spans (in-memory, per request)
  -> recorder closes trace
  -> ring buffer (live, fixed size) -> SSE clients
  -> write queue -> writer thread -> batched SQLite insert (traces + observations + scores)
                                  -> rollup update (10s buckets)
retention timer (60s)
  -> prune traces/logs beyond caps
  -> roll up 10s -> 1m -> 1h
  -> checkpoint WAL, refresh planner stats
```

- Sampling (`LAYWATCH_TRACE_SAMPLE`, default 1.0) applies to successful traces only. Errors, refusals
  and playground runs are always recorded. Sampled-out traces still update rollups, so metrics stay
  accurate while detail is skipped; the trace is marked `meta.sampled = false`.
- Batches flush at 200 rows or 250 ms. Inserts are wrapped in one transaction; `synchronous=NORMAL`
  under WAL is the documented durability tradeoff (a hard crash can lose the last tick, never corrupt).

## 6. Rollups and metric definitions

| Metric | Definition | Bucket fields |
|---|---|---|
| `requests` | count of traces | count, by model and route |
| `errors` | count where `status >= 400`, split `4xx`/`5xx` | count, by status |
| `latency` | `duration_ms` distribution | count, sum, min, max, p50, p90, p95, p99 |
| `queue` | `queue_ms` distribution | count, sum, min, max, p50, p95 |
| `forward` | `forward_ms` distribution | count, sum, min, max, p50, p95 |
| `rss` | process RSS sampled each tick | last, max |

Percentiles are computed from the same 10 s bucket sample set stored in memory for the tick (bounded to
the ring size), and persisted per bucket. Charts read 10 s buckets for windows <= 15 m, 1 m buckets for
<= 6 h, 1 h buckets beyond that. The API chooses the step; the client never re-buckets.

Rollup accuracy statement shown in the UI: percentile values are bucket-level, not exact global
percentiles. The Metrics view states this in its section meta.

## 7. Log capture

- Sources: `server`, `auth`, `engine`, `store`, `retention`, `ui` (playground actions).
- Format (ring and SQLite): `ts`, `level`, `source`, `trace_id`, `message`. The human line keeps the
  existing shape (`2026-09-22 14:31:07 predict request_id=9f2c1a4b status=200 ms=412.5 model=english`).
- Levels: `debug`, `info`, `warn`, `error`. Default level `info`; `debug` is opt-in per source.
- Every response error logs at `warn` or `error` with the trace id, so the Logs view and the trace
  detail page correlate by id in both directions.

## 8. Scores

Scores attach judgements to traces. v0.1 sources:

- `playground`: a run from the console can be scored by the operator (`helpful` boolean, `quality`
  1 to 5). Playground runs are traces with `session_id` set and `meta.source = "playground"`.
- `api`: `POST /api/v1/traces/{id}/scores` accepts numeric, boolean or categorical values with a name
  and comment.

No annotation queue, no dataset management, no LLM-as-judge in v0.1. The model exists so later work
does not need a migration.

## 9. Self-observability

LayaWatch reports its own health in `/api/v1/meta` and the sidebar footer:

| Signal | Meaning |
|---|---|
| `obs_dropped_total` | traces dropped by backpressure or sampling |
| `write_queue_depth` | pending rows waiting for the writer |
| `write_latency_ms` | last batch duration |
| `db_size_bytes` | SQLite file size |
| `rss_mb` | process RSS (existing `_rss_mb`) |
| `ring_usage` | live ring occupancy |
| `sse_clients` | connected stream clients |

These appear in the Overview "System pulse" strip and in Settings under Diagnostics.

## 10. Growth estimates (used to justify budgets)

At 4 req/s sustained with 9 observations per trace:

| Horizon | Traces | Observations | SQLite size |
|---|---|---|---|
| 1 hour | 14,400 | 129,600 | ~35 MB |
| 1 day | 345,600 | 3.1 M | ~840 MB (exceeds cap, pruning keeps ~10k traces / ~45 MB) |
| Steady state with defaults | 10,000 traces | 90,000 observations | ~45 to 60 MB |

The default caps are chosen so a laptop deployment stays under 200 MB of LayaWatch disk. Operators who
want a full day of history raise `LAYWATCH_RETENTION_TRACES` knowingly; the Settings page states the
resulting estimate before saving.
