-- Rev 3 (design "Bounded Performance" / "Additive migration"): additive
-- index only — NO table, column, or row change. Lets SQLite seek directly
-- to the (flow_id, device_id, mode) partition and read it in `started_at`
-- order for the `compare` baseline query, instead of scanning the entire
-- flow+device partition (warm AND cold) and filtering `mode` as a residual
-- predicate (the existing `idx_run_flow_device_time` index lacks `mode`).
-- Picked up by the existing `PRAGMA user_version`-driven migration runner
-- (`SqliteStore._migrate`) — mirrored into `db/schema.sql` so fresh and
-- migrated DBs converge on the same shape.
CREATE INDEX IF NOT EXISTS idx_run_baseline ON run(flow_id, device_id, mode, started_at);
