-- Migration 0001: initial schema.
-- Mirrors db/schema.sql's DDL (master design §9.2, §9.3) for existing
-- stores brought up via the migration runner (§9.5). Connection pragmas
-- (foreign_keys, journal_mode, busy_timeout) and the `PRAGMA user_version`
-- bump are NOT set here — the migration runner (`adapters/store_sqlite.py`,
-- PR2) applies pragmas per-connection and bumps `user_version` after this
-- file runs, inside the same transaction.

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
  metric_id   INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,        -- '/loans' (stable template, NOT paths with IDs)
  unit        TEXT NOT NULL DEFAULT 'ms'
);

-- ===== FACTS =====
CREATE TABLE run (
  run_id        INTEGER PRIMARY KEY,
  flow_id       INTEGER NOT NULL REFERENCES flow(flow_id),
  device_id     INTEGER NOT NULL REFERENCES device(device_id),
  started_at    TEXT    NOT NULL,          -- ISO-8601 UTC (sorts chronologically as text)
  iterations    INTEGER NOT NULL,          -- to detect partial coverage (n < iterations)
  mode          TEXT    NOT NULL DEFAULT 'warm',   -- 'warm' | 'cold'
  source        TEXT    NOT NULL,          -- 'ci' | 'local:eduardo' (keeps series apart)
  git_commit    TEXT,   git_branch   TEXT, -- bash-owned
  app_version   TEXT,                      -- app-owned ([PERF-META])
  is_dev_bundle INTEGER,                   -- app-owned: 0/1 — trustworthy build?
  bundle_source TEXT,                      -- app-owned: 'dev-server' | 'embedded'
  build_variant TEXT,   tool_version TEXT
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
-- Flashlight FPS/CPU/RAM per iteration (NO network — that's Embrace). Fixed columns > EAV.
CREATE TABLE system_sample (
  iteration_id INTEGER PRIMARY KEY REFERENCES iteration(iteration_id) ON DELETE CASCADE,
  fps_avg REAL, cpu_pct_avg REAL, ram_mb_avg REAL
);

-- ===== INDEXES (driven by the "this metric, this device, over time" query) =====
CREATE INDEX idx_run_flow_device_time ON run(flow_id, device_id, started_at);
CREATE INDEX idx_measure_metric       ON measure(metric_id);
CREATE INDEX idx_measure_run          ON measure(run_id);

-- ===== VIEWS =====
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
