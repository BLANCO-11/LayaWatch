-- LayaWatch schema: complete DDL for a fresh database.
-- Reference copy: the migrations in migrations/ produce exactly this table set.
-- migrations bookkeeping
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);

CREATE TABLE traces (
  id            TEXT PRIMARY KEY,            -- 8 hex chars
  ts_start      REAL NOT NULL,               -- epoch seconds, float
  duration_ms   REAL NOT NULL,
  route         TEXT NOT NULL,               -- /predict | /route
  method        TEXT NOT NULL,
  status        INTEGER NOT NULL,
  model         TEXT,
  route_reason  TEXT,
  lang          TEXT,
  queue_ms      REAL NOT NULL DEFAULT 0,
  forward_ms    REAL,
  state_bytes   INTEGER,
  question_count INTEGER,
  client_key_id TEXT,
  session_id    TEXT,
  error_code    TEXT,
  error_message TEXT,
  tags          TEXT,                        -- JSON array
  meta          TEXT                         -- JSON object
);
CREATE INDEX traces_ts ON traces(ts_start DESC, id DESC);
CREATE INDEX traces_status_ts ON traces(status, ts_start DESC, id DESC);
CREATE INDEX traces_route_ts ON traces(route, ts_start DESC, id DESC);
CREATE INDEX traces_model_ts ON traces(model, ts_start DESC, id DESC);

CREATE TABLE observations (
  id          TEXT PRIMARY KEY,
  trace_id    TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
  parent_id   TEXT,
  name        TEXT NOT NULL,                 -- vocabulary name
  type        TEXT NOT NULL,                 -- span | generation | event
  start_ms    REAL NOT NULL,                 -- offset from trace start
  duration_ms REAL NOT NULL,
  status      TEXT NOT NULL,                 -- ok | error
  model       TEXT,
  input       TEXT,                          -- truncated JSON, null unless capture enabled
  output      TEXT,
  meta        TEXT
);
CREATE INDEX observations_trace ON observations(trace_id, start_ms, id);

CREATE TABLE scores (
  id        TEXT PRIMARY KEY,
  trace_id  TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
  name      TEXT NOT NULL,
  value     REAL NOT NULL,
  data_type TEXT NOT NULL,                   -- numeric | boolean | categorical
  source    TEXT NOT NULL,                   -- human | api | playground
  comment   TEXT,
  ts        REAL NOT NULL
);
CREATE INDEX scores_trace ON scores(trace_id);

CREATE TABLE metric_rollup (
  bucket    INTEGER NOT NULL,                -- epoch seconds, aligned
  step      INTEGER NOT NULL,                -- 10 | 60 | 3600
  metric    TEXT NOT NULL,                   -- requests | errors | latency | queue
  model     TEXT NOT NULL DEFAULT '',
  route     TEXT NOT NULL DEFAULT '',
  status    INTEGER NOT NULL DEFAULT 0,
  count     INTEGER NOT NULL,
  sum       REAL NOT NULL,
  min       REAL NOT NULL,
  max       REAL NOT NULL,
  p50       REAL, p90 REAL, p95 REAL, p99 REAL,
  PRIMARY KEY (bucket, step, metric, model, route, status)
);
CREATE INDEX rollup_metric_bucket ON metric_rollup(metric, step, bucket DESC);

CREATE TABLE log_entry (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       REAL NOT NULL,
  level    TEXT NOT NULL,
  source   TEXT NOT NULL,
  trace_id TEXT,
  message  TEXT NOT NULL
);
CREATE INDEX log_ts ON log_entry(ts DESC, id DESC);

CREATE TABLE users (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  role          TEXT NOT NULL,               -- owner | admin | viewer
  password_hash TEXT NOT NULL,               -- scrypt
  created_at    REAL NOT NULL,
  last_login_at REAL,
  disabled      INTEGER NOT NULL DEFAULT 0,
  must_change   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE sessions (
  id         TEXT PRIMARY KEY,               -- hashed token
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  last_seen  REAL NOT NULL,
  user_agent TEXT,
  ip         TEXT,
  revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX sessions_user ON sessions(user_id);

CREATE TABLE api_keys (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  prefix     TEXT NOT NULL,
  hash       TEXT NOT NULL,                  -- HMAC-SHA256(secret, server pepper)
  created_at REAL NOT NULL,
  last_used  REAL,
  revoked_at REAL,
  request_count INTEGER NOT NULL DEFAULT 0,
  rate_limit_per_min INTEGER,                -- null: use the global default
  burst      INTEGER                         -- null: burst = limit
);

CREATE TABLE audit_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       REAL NOT NULL,
  actor_id TEXT,
  actor    TEXT NOT NULL,                    -- email or "system"
  action   TEXT NOT NULL,                    -- key.created, user.role_changed, ...
  target   TEXT,
  result   TEXT NOT NULL,                    -- ok | denied | error
  meta     TEXT
);
CREATE INDEX audit_ts ON audit_log(ts DESC, id DESC);

CREATE TABLE settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at REAL NOT NULL,
  updated_by TEXT
);
