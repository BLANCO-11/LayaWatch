/* Typed fetch client for the LayaWatch API (base path /api/v1).
 * Contract: docs/api-reference.md sections 1-2 and 10.
 * - Session cookie auth uses credentials: "same-origin".
 * - Mutating requests attach X-CSRF-Token read from the lw_csrf cookie.
 * - Errors follow the { error: { code, message, details } } envelope.
 * - 401 signals session expiry; the AuthProvider turns it into a /login redirect.
 */

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;
  readonly requestId?: string;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, unknown>,
    requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

const MUTATING: Record<string, true> = { POST: true, PUT: true, PATCH: true, DELETE: true };

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const parts = document.cookie.split(";");
  for (const part of parts) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === name) return decodeURIComponent(part.slice(eq + 1).trim());
  }
  return null;
}

function parseErrorBody(status: number, payload: unknown): ApiErrorBody {
  const fallback = { code: `http_${status}`, message: `Request failed (${status})` };
  if (payload === null || typeof payload !== "object" || !("error" in payload)) return fallback;
  const err = payload.error;
  if (err === null || typeof err !== "object") return fallback;
  const code = "code" in err && typeof err.code === "string" ? err.code : fallback.code;
  const message =
    "message" in err && typeof err.message === "string" ? err.message : fallback.message;
  let details: Record<string, unknown> | undefined;
  if ("details" in err && err.details !== null && typeof err.details === "object") {
    details = {};
    for (const [key, value] of Object.entries(err.details)) details[key] = value;
  }
  return { code, message, details };
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (MUTATING[method]) {
    const csrf = readCookie("lw_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const res = await fetch(path, {
    method,
    headers,
    body,
    credentials: "same-origin",
    signal: opts.signal,
  });
  const requestId = res.headers.get("X-Request-Id") ?? undefined;
  const contentType = res.headers.get("Content-Type") ?? "";
  let payload: unknown = null;
  let bodyFailed = false;
  if (contentType.includes("application/json")) {
    try {
      payload = await res.json();
    } catch {
      /* A 200 with an unreadable body is a transport failure, not empty data:
       * returning {} here would let consumers crash on missing fields. */
      bodyFailed = true;
    }
  }
  if (!res.ok) {
    const err = parseErrorBody(res.status, payload);
    throw new ApiError(res.status, err.code, err.message, err.details, requestId);
  }
  if (bodyFailed) {
    throw new ApiError(
      res.status,
      `http_${res.status}`,
      "The response body could not be read. Retry the request.",
      undefined,
      requestId,
    );
  }
  return (payload ?? {}) as T;
}

export const api = {
  get: <T>(path: string, opts?: Omit<RequestOptions, "method" | "body">) =>
    apiFetch<T>(path, { ...opts, method: "GET" }),
  post: <T>(path: string, body?: unknown, opts?: Omit<RequestOptions, "method" | "body">) =>
    apiFetch<T>(path, { ...opts, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, opts?: Omit<RequestOptions, "method" | "body">) =>
    apiFetch<T>(path, { ...opts, method: "PATCH", body }),
  del: <T>(path: string, opts?: Omit<RequestOptions, "method" | "body">) =>
    apiFetch<T>(path, { ...opts, method: "DELETE" }),
};

/* ---------- API shapes (docs/api-reference.md sections 3-13) ---------- */

export interface TraceSummary {
  id: string;
  ts_start: number;
  duration_ms: number;
  route: string;
  method: string;
  status: number;
  model: string;
  route_reason: string;
  queue_ms: number;
  forward_ms: number;
  client_key_id: string;
  span_count: number;
  error_code: string | null;
}

/* One observation row (GET /api/v1/traces/{id}.observations items share this shape).
 * `meta` carries the validated vocabulary attributes for the span name. */
export interface Observation {
  id?: string;
  trace_id?: string;
  parent_id?: string | null;
  name: string;
  type: "span" | "generation" | "event";
  start_ms: number;
  duration_ms: number;
  status: "ok" | "error";
  model?: string | null;
  input?: string | null;
  output?: string | null;
  meta: Record<string, unknown> | null;
}

export interface TraceScore {
  id: string;
  trace_id: string;
  name: string;
  value: number | string;
  data_type: "numeric" | "boolean" | "categorical";
  source: string;
  comment?: string | null;
  ts: number;
}

/* Server-computed block on the detail envelope (queries.trace_detail). */
export interface TraceSummaryBlock {
  framework_ms: number;
  forward_ms: number | null;
  forward_share: number;
  queue_share: number;
  span_count: number;
  status_class: string;
}

/* Trace row plus detail-only columns. */
export interface TraceRow extends TraceSummary {
  lang?: string | null;
  state_bytes?: number | null;
  question_count?: number | null;
  session_id?: string | null;
  error_message?: string | null;
  tags?: string[];
  meta?: Record<string, unknown>;
}

/* GET /api/v1/traces/{id} envelope. */
export interface TraceDetail {
  trace: TraceRow;
  observations: Observation[];
  scores: TraceScore[];
  logs: LogEntry[];
  summary: TraceSummaryBlock;
}

export interface Paged<T> {
  items: T[];
  next_cursor: string | null;
  total_estimate: number;
}

export type MetricPoint = [number, number | Record<string, number>];

export interface MetricsResponse {
  step: number;
  window: { since: number; until: number };
  series: Array<{ metric: string; points: MetricPoint[] }>;
  note?: string;
}

/* GET /api/v1/metrics/summary (queries.metrics_summary): window scalars plus
 * previous-window deltas; `throttled_5m` is present only when non-zero. */
export interface MetricsSummary {
  range_s: number;
  window: { since: number; until: number };
  prev_window: { since: number; until: number };
  requests_per_s: number;
  requests_per_s_prev: number;
  errors: number;
  errors_prev: number;
  errors_by_status: Record<string, number>;
  p50: number;
  p50_prev: number;
  p95: number;
  p95_prev: number;
  queue_ms: number;
  queue_ms_prev: number;
  deltas: {
    requests_per_s: number;
    errors: number;
    p50: number;
    p95: number;
    queue_ms: number;
  };
  total_requests: number;
  throttled_5m?: number;
}

/* GET /api/v1/logs items and SSE `log` events (log ring entry). */
export interface LogEntry {
  id?: number;
  ts: number;
  level: string;
  source?: string;
  trace_id?: string | null;
  message: string;
}

/* GET /api/v1/metrics/models (queries.metrics_models). */
export interface ModelsMixResponse {
  step: number;
  window: { since: number; until: number };
  buckets: Array<{ ts: number; counts: Record<string, number> }>;
  models: string[];
  total: number;
}

/* GET /healthz. */
export interface HealthResponse {
  status: string;
  models: string[];
  device: string;
  auth_enabled: boolean;
}

export interface StreamPulse {
  requests_per_s: number;
  errors_5m: number;
  p50: number;
  p95: number;
  queue_ms: number;
  rss_mb: number;
  dropped_total: number;
  queue_depth: number;
}

export interface MeResponse {
  id: string;
  email: string;
  name?: string;
  role: "owner" | "admin" | "viewer";
  permissions?: string[];
}

export interface StreamHello {
  server_time: number;
  tick: number;
  ring_size: number;
}

export interface MetaConfig {
  bind?: string;
  port?: number;
  device?: string;
  models?: string[];
  english_only?: boolean;
  stream_tick?: number;
  ring_traces?: number;
  retention_traces?: number;
  retention_days?: number;
  [key: string]: unknown;
}

export interface MetaResponse {
  version: string;
  uptime_s: number;
  started_at: number;
  db_size_bytes?: number;
  rss_mb?: number;
  sse_clients?: number;
  device?: string;
  auth_enabled?: boolean;
  /* writer counters spread at the top level (health.add_meta_routes). */
  obs_dropped_total?: number;
  logs_dropped_total?: number;
  writes_total?: number;
  write_queue_depth?: number;
  write_latency_ms?: number;
  config?: MetaConfig;
  deprecation?: Record<string, number>;
  [key: string]: unknown;
}


export interface ApiKeyItem {
  id: string;
  name: string;
  prefix: string;
  created: string;
  last_used?: string | null;
  request_count: number;
  revoked: boolean;
}

export interface ModelInfo {
  loaded: string[];
  available: string[];
  default: string[];
  device: string;
  rss_mb: number;
}
