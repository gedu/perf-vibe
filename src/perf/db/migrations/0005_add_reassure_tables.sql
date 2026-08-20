-- Migration 0005: add reassure ingest tables (reassure-ingest design, DDL).
-- Additive only. Connection pragmas and the `PRAGMA user_version` bump are
-- NOT set here — the migration runner (`adapters/store_sqlite.py`) applies
-- pragmas per-connection and bumps `user_version` after this file runs,
-- inside the same transaction (matches `migrations/0001_init.sql:3-7`).

CREATE TABLE reassure_import (
  import_id    INTEGER PRIMARY KEY,
  content_hash TEXT NOT NULL UNIQUE,   -- sha256 of RAW file bytes = the whole idempotency key
  imported_at  TEXT NOT NULL,          -- ISO-8601 UTC from the injected Clock
  source_path  TEXT NOT NULL,          -- internal storage name; the PAYLOAD key is `path`
  branch       TEXT, commit_hash TEXT, created_date TEXT   -- optional header metadata
);
CREATE TABLE reassure_entry (
  entry_id          INTEGER PRIMARY KEY,
  import_id         INTEGER NOT NULL REFERENCES reassure_import(import_id) ON DELETE CASCADE,
  name              TEXT NOT NULL,     -- Jest `describe > test` chain; the ONLY identity.
                                       -- Intentionally NOT UNIQUE(import_id, name).
  entry_type        TEXT NOT NULL DEFAULT 'render',
  runs              INTEGER NOT NULL,  -- reassure's DECLARED runs (== len(counts))
  warmup_durations  TEXT,              -- verbatim JSON passthrough, diagnostic only.
  outlier_durations TEXT               -- NULL = key ABSENT; '[]' = present but empty.
);
-- LOAD-BEARING: durations[] and counts[] are NOT index-aligned and MUST NEVER be zipped
-- (durations = outlier-FILTERED set, counts = UNFILTERED post-warmup set, removal ON by
-- default). Each `idx` is an ordinal WITHIN ITS OWN SERIES: not a run id, not comparable
-- across the two tables. Neither table needs an extra index (UNIQUE is entry_id-leading).
CREATE TABLE reassure_duration_sample (
  duration_sample_id INTEGER PRIMARY KEY,
  entry_id           INTEGER NOT NULL REFERENCES reassure_entry(entry_id) ON DELETE CASCADE,
  idx                INTEGER NOT NULL,
  duration_ms        REAL NOT NULL,
  UNIQUE (entry_id, idx)
);
CREATE TABLE reassure_count_sample (
  count_sample_id INTEGER PRIMARY KEY,
  entry_id        INTEGER NOT NULL REFERENCES reassure_entry(entry_id) ON DELETE CASCADE,
  idx             INTEGER NOT NULL,
  render_count    REAL NOT NULL,   -- REAL, not INTEGER: reassure types it `number[]`
  UNIQUE (entry_id, idx)
);
CREATE INDEX idx_reassure_entry_name   ON reassure_entry(name, import_id);
CREATE INDEX idx_reassure_entry_import ON reassure_entry(import_id);
CREATE INDEX idx_reassure_import_time  ON reassure_import(imported_at);
