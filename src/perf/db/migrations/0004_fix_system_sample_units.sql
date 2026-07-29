-- Fix the persisted `metric.unit` for system-sample aggregate metrics
-- (resilience batch, Task 5). `run`'s ingestion (`SqliteStore._upsert_metrics`)
-- historically wrote unit='ms' for EVERY metric, including fps/ram/cpu
-- aggregates — so any external reader of the `metric` table saw "ms" for
-- fps_avg. The analyzer patched units in memory via
-- `analyzer_sql._SYSTEM_SAMPLE_UNITS`, but the persisted rows stayed wrong.
--
-- This data-only migration corrects the unit on already-written rows to match
-- `_SYSTEM_SAMPLE_UNITS` (fps/mb/pct); total_time_ms/start_time_ms are already
-- 'ms' so they need no change. Idempotent (re-running is a no-op). Fresh DBs
-- have no metric rows yet, so this affects nothing there — the corrected unit
-- is now written at ingestion time by `_upsert_metrics`. No schema/DDL change,
-- so `db/schema.sql` needs no mirror. Picked up by the existing
-- `PRAGMA user_version`-driven migration runner (`SqliteStore._migrate`).
UPDATE metric SET unit = 'fps' WHERE name IN ('fps_avg', 'fps_min');
UPDATE metric SET unit = 'mb'  WHERE name IN ('ram_avg_mb', 'ram_peak_mb');
UPDATE metric SET unit = 'pct' WHERE name IN ('cpu_avg_pct', 'cpu_peak_pct');
