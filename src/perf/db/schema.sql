-- Canonical schema for the `perf` local store (master design §9.2, §9.3).
-- Represents the CURRENT full schema for FRESH databases. Existing
-- databases are brought here via `db/migrations/*.sql`, driven by
-- `PRAGMA user_version` — never edit this file in place for a store that
-- already has data; add a numbered migration instead (§9.5).

PRAGMA foreign_keys = ON;        -- per connection
PRAGMA journal_mode = WAL;       -- writer + reader coexist
PRAGMA busy_timeout = 5000;      -- avoid SQLITE_BUSY with concurrent runs
-- PRAGMA user_version = 2;      -- schema version for migrations (set by migration runner)

-- ===== DIMENSIONS (deduped context) =====
CREATE TABLE device (
  device_id   INTEGER PRIMARY KEY,
  device_key  TEXT NOT NULL UNIQUE,        -- 'Pixel 8 Pro|Android 14|physical'
  model       TEXT NOT NULL,
  os_version  TEXT NOT NULL,
  is_emulator INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE flow (
  flow_id     INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,        -- 'prestamos-warm'
  description TEXT
);
CREATE TABLE metric (
  metric_id        INTEGER PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,        -- '/loans' (stable template, NOT paths with IDs)
  unit             TEXT NOT NULL DEFAULT 'ms',
  higher_is_better INTEGER NOT NULL DEFAULT 0    -- direction metadata: 0=lower-is-better (default), 1=higher-is-better (e.g. fps_avg/fps_min)
);

-- ===== FACTS =====
CREATE TABLE run (
  run_id          INTEGER PRIMARY KEY,
  flow_id         INTEGER NOT NULL REFERENCES flow(flow_id),
  device_id       INTEGER NOT NULL REFERENCES device(device_id),
  started_at      TEXT    NOT NULL,          -- ISO-8601 UTC (sorts chronologically as text)
  iterations      INTEGER NOT NULL,          -- to detect partial coverage (n < iterations)
  mode            TEXT    NOT NULL DEFAULT 'warm',   -- 'warm' | 'cold'
  source          TEXT    NOT NULL,          -- 'ci' | 'local:eduardo' (keeps series apart)
  git_commit      TEXT,   git_branch   TEXT, -- bash-owned
  app_version     TEXT,                      -- app-owned ([PERF-META])
  is_dev_bundle   INTEGER,                   -- app-owned: 0/1 — trustworthy build?
  bundle_source   TEXT,                      -- app-owned: 'dev-server' | 'embedded'
  build_variant   TEXT,   tool_version TEXT,
  raw_report_path TEXT                       -- disk path to the Flashlight results JSON (one report per run); NULL when no sampler ran
);
CREATE TABLE iteration (                   -- only for data Flashlight buckets cleanly
  iteration_id INTEGER PRIMARY KEY,
  run_id       INTEGER NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  idx          INTEGER NOT NULL,
  status       TEXT NOT NULL DEFAULT 'success',
  UNIQUE (run_id, idx)
);
-- Markers hang off the RUN, not the iteration: the logcat stream is flat and
-- cannot be reliably bucketed into Flashlight iterations. Percentiles are
-- computed over all of a run's measures — the iteration isn't needed for them.
CREATE TABLE measure (
  measure_id  INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  metric_id   INTEGER NOT NULL REFERENCES metric(metric_id),
  duration_ms REAL NOT NULL
);
-- Flashlight FPS/CPU/RAM per iteration (NO network — that's Embrace). Fixed
-- columns > EAV. Aggregated from the per-sample time-series (§37/§39):
-- total_time_ms/start_time_ms are the iteration's own fields; fps/ram/cpu
-- are avg+min/peak over the measures[] series.
CREATE TABLE system_sample (
  iteration_id  INTEGER PRIMARY KEY REFERENCES iteration(iteration_id) ON DELETE CASCADE,
  total_time_ms REAL,  -- iteration.time (total flow duration)
  start_time_ms REAL,  -- iteration.startTime (app/screen startup)
  fps_avg       REAL,
  fps_min       REAL,  -- worst jank moment
  ram_avg_mb    REAL,
  ram_peak_mb   REAL,  -- peak growth = leak signal
  cpu_avg_pct   REAL,
  cpu_peak_pct  REAL   -- sum of cpu.perName per sample, then avg/peak across the series
);

-- ===== INDEXES (driven by the "this metric, this device, over time" query) =====
CREATE INDEX idx_run_flow_device_time ON run(flow_id, device_id, started_at);
CREATE INDEX idx_measure_metric       ON measure(metric_id);
CREATE INDEX idx_measure_run          ON measure(run_id);
-- Rev 3 (0002_compare_baseline_index.sql): the `compare` baseline query
-- additionally filters by `mode` — this index lets it seek directly to the
-- (flow_id, device_id, mode) partition instead of scanning the whole
-- flow+device history and filtering `mode` as a residual predicate.
CREATE INDEX idx_run_baseline ON run(flow_id, device_id, mode, started_at);

-- ===== VIEWS =====
-- Per-run + metric summary (nearest-rank percentiles) — §9.3.
-- SQLite has no PERCENTILE_CONT; rank within the group.
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
       MAX(CASE WHEN rn <= CAST(0.9*n AS INT) THEN duration_ms END) AS p90_ms
FROM ranked GROUP BY run_id, metric_id;
