-- 0003_covering_list_indexes: widen every docs/api-reference.md list index with the exact
-- ORDER BY tiebreak column (docs/performance.md section 4.3 items 13/14).
-- The 0001_init keys stay leading columns; the trailing ``id`` makes each index's order
-- match the list query's ORDER BY exactly, so list pages scan the index with no temp
-- b-tree and the keyset cursor predicate is one index seek at any depth.
DROP INDEX traces_ts;
CREATE INDEX traces_ts ON traces(ts_start DESC, id DESC);

DROP INDEX traces_status_ts;
CREATE INDEX traces_status_ts ON traces(status, ts_start DESC, id DESC);

DROP INDEX traces_route_ts;
CREATE INDEX traces_route_ts ON traces(route, ts_start DESC, id DESC);

DROP INDEX traces_model_ts;
CREATE INDEX traces_model_ts ON traces(model, ts_start DESC, id DESC);

DROP INDEX observations_trace;
CREATE INDEX observations_trace ON observations(trace_id, start_ms, id);

DROP INDEX log_ts;
CREATE INDEX log_ts ON log_entry(ts DESC, id DESC);

DROP INDEX audit_ts;
CREATE INDEX audit_ts ON audit_log(ts DESC, id DESC);
