-- Fix the `run_metric_summary.p90_ms` percentile bug (math / anti-false-
-- positive batch, Task 1). The Rev 2 view used floor nearest-rank
-- `rn <= CAST(0.9*n AS INT)`, which for n=2 selects rank floor(1.8)=1 — the
-- MINIMUM — as "p90", a systematic OPTIMISTIC bias that hides regressions.
-- The domain convention (`domain/statistics.percentile`, CEIL nearest-rank)
-- is the correct one, and the analyzer already uses it for the system_sample
-- family; this migration makes the SQL view MATCH it for the measure family.
--
-- Integer CEIL nearest-rank: `rn <= (9*n + 9) / 10`. SQLite integer division
-- truncates toward zero, and for positive integers
--   floor((9n + 9) / 10) == ceil(9n / 10) == ceil(0.9*n)
-- (the standard `ceil(a/b) = floor((a + b - 1) / b)` identity with b=10).
-- Verified: n=1->1, n=2->2, n=5->5, n=10->9, n=20->18 — matching
-- `math.ceil(0.9*n)`. A view cannot be altered in place, so DROP + recreate;
-- picked up by the existing `PRAGMA user_version`-driven migration runner
-- (`SqliteStore._migrate`) and mirrored into `db/schema.sql` so fresh and
-- migrated DBs converge on the corrected shape.
DROP VIEW IF EXISTS run_metric_summary;
CREATE VIEW run_metric_summary AS
WITH ranked AS (
  SELECT run_id, metric_id, duration_ms,
         ROW_NUMBER() OVER (PARTITION BY run_id, metric_id ORDER BY duration_ms) AS rn,
         COUNT(*)     OVER (PARTITION BY run_id, metric_id)                      AS n
  FROM measure
)
SELECT run_id, metric_id, n,
       MIN(duration_ms) AS min_ms, MAX(duration_ms) AS max_ms, AVG(duration_ms) AS avg_ms,
       AVG(CASE WHEN rn IN ((n+1)/2,(n+2)/2) THEN duration_ms END) AS p50_ms,
       MAX(CASE WHEN rn <= (9*n + 9) / 10 THEN duration_ms END) AS p90_ms
FROM ranked GROUP BY run_id, metric_id;
