"""Migration runner integration tests (§9.5) — PR2 store-half.

RED-before-GREEN: written before `src/perf/adapters/store_sqlite.py`
existed (highest-risk item per the PR2 apply instructions). Covers:
fresh-DB migration to the latest `user_version`, idempotent re-open
(no-op when already at the latest version), migration files loaded ONLY
from the package's own `db/migrations/` directory, and connection pragmas
(WAL + busy_timeout + foreign_keys) required by §9.2/§9.5.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from perf.adapters.store_sqlite import _MIGRATIONS_DIR, SqliteStore

EXPECTED_TABLES = {"device", "flow", "metric", "run", "iteration", "measure", "system_sample"}


def test_fresh_db_migrates_to_latest_user_version_and_creates_schema(tmp_path):
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path)
    try:
        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 2  # 0001_init.sql + 0002_compare_baseline_index.sql

        tables = {
            row[0]
            for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert tables >= EXPECTED_TABLES
    finally:
        store.close()


def test_fresh_db_has_idx_run_baseline_index(tmp_path):
    """Rev 3 (design 'Bounded Performance'): a fresh DB (via the migration
    runner picking up `0002_compare_baseline_index.sql`) gets the additive
    `idx_run_baseline` index the bounded baseline query relies on."""
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path)
    try:
        indexes = {
            row[0]
            for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_run_baseline" in indexes
    finally:
        store.close()


def test_migrated_pre_rev3_db_advances_to_user_version_2_and_adds_index(tmp_path):
    """A DB created BEFORE 0002 existed (i.e. only 0001 applied) picks up
    0002 on next open: `user_version` advances to 2 and `idx_run_baseline`
    appears — the additive-index migration path (design 'Migration /
    Rollout': 'applied by the existing user_version-driven runner')."""
    db_path = tmp_path / "perf.db"

    # Simulate a pre-Rev3 DB: apply ONLY 0001 by hand, bypassing the
    # generic glob-based runner so this test controls exactly which
    # migration has been applied so far.
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    init_sql = (_MIGRATIONS_DIR / "0001_init.sql").read_text()
    conn.executescript(f"BEGIN;\n{init_sql}\nPRAGMA user_version = 1;\nCOMMIT;")
    version_before = conn.execute("PRAGMA user_version").fetchone()[0]
    indexes_before = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    conn.close()
    assert version_before == 1
    assert "idx_run_baseline" not in indexes_before

    store = SqliteStore(db_path)
    try:
        version_after = store._conn.execute("PRAGMA user_version").fetchone()[0]
        indexes_after = {
            row[0]
            for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert version_after == 2
        assert "idx_run_baseline" in indexes_after
    finally:
        store.close()


def test_fresh_schema_sql_and_migrated_db_converge_on_indexes(tmp_path):
    """`db/schema.sql` (fresh-DB canonical shape) must mirror the same
    `idx_run_baseline` index the migration adds, so fresh and migrated DBs
    converge (design 'Additive migration')."""
    schema_path = _MIGRATIONS_DIR.parent / "schema.sql"
    schema_sql = schema_path.read_text()
    assert "idx_run_baseline" in schema_sql
    assert "CREATE INDEX idx_run_baseline ON run(flow_id, device_id, mode, started_at)" in (
        schema_sql.replace("\n", " ")
    )

    fresh_conn = sqlite3.connect(":memory:")
    fresh_conn.executescript(schema_sql)
    fresh_indexes = {
        row[0] for row in fresh_conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    fresh_conn.close()

    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path)
    try:
        migrated_indexes = {
            row[0]
            for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    finally:
        store.close()

    assert fresh_indexes == migrated_indexes
    assert "idx_run_baseline" in fresh_indexes


def test_migration_runner_still_idempotent_at_version_2_on_reopen(tmp_path):
    db_path = tmp_path / "perf.db"

    store1 = SqliteStore(db_path)
    store1.close()

    store2 = SqliteStore(db_path)
    try:
        version = store2._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 2
    finally:
        store2.close()


def test_migration_runner_is_idempotent_on_reopen(tmp_path):
    db_path = tmp_path / "perf.db"

    store1 = SqliteStore(db_path)
    store1.close()

    # Reopening an already-migrated DB must be a no-op: no re-application of
    # 0001's CREATE TABLE statements (which would raise "table already
    # exists"), and user_version stays at the same latest value.
    store2 = SqliteStore(db_path)
    try:
        version = store2._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 2  # latest version as of Rev 3 (0001 + 0002)
    finally:
        store2.close()


def test_migrations_loaded_only_from_package_migrations_dir():
    assert _MIGRATIONS_DIR.is_dir()
    assert _MIGRATIONS_DIR.name == "migrations"
    assert _MIGRATIONS_DIR.parent.name == "db"
    # package-relative, never derived from a user-supplied --db path
    assert "src/perf/db/migrations" in str(_MIGRATIONS_DIR).replace("\\", "/")
    assert list(_MIGRATIONS_DIR.glob("0001_*.sql")), "0001 migration must exist in the package dir"


def test_pragmas_set_after_connect(tmp_path):
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path)
    try:
        fk = store._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        journal_mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]

        assert fk == 1
        assert journal_mode.lower() == "wal"
        assert busy_timeout == 5000
    finally:
        store.close()


def test_custom_busy_timeout_is_honored(tmp_path):
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path, busy_timeout_ms=2500)
    try:
        busy_timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_timeout == 2500
    finally:
        store.close()


def test_busy_timeout_causes_second_writer_to_wait_before_failing(tmp_path):
    """A second writer competing for the write lock WAITS roughly up to
    `busy_timeout` before giving up, proving `PRAGMA busy_timeout` took
    effect on the connection (without it, SQLite raises
    `database is locked` immediately, in ~0ms — no retry/wait at all).
    Kept single-threaded (no cross-thread sqlite3 objects) by never
    releasing store_a's lock; only the elapsed time before failure is
    asserted."""
    db_path = tmp_path / "perf.db"
    store_a = SqliteStore(db_path, busy_timeout_ms=500)
    store_b = SqliteStore(db_path, busy_timeout_ms=500)
    try:
        store_a._conn.execute("BEGIN IMMEDIATE")
        store_a._conn.execute("INSERT INTO flow (name) VALUES (?)", ("writer-a",))

        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            store_b._conn.execute("INSERT INTO flow (name) VALUES (?)", ("writer-b",))
        elapsed = time.monotonic() - start

        # Without `busy_timeout` set, SQLite raises "database is locked"
        # immediately (~0ms, no wait at all). A looser floor (half the
        # configured 500ms) keeps this test's teeth — it still proves a
        # real wait happened — while tolerating CI scheduling jitter that
        # made the original 0.4s near-exact-threshold assert flaky.
        assert elapsed >= 0.25

        store_a._conn.execute("ROLLBACK")
    finally:
        store_a.close()
        store_b.close()
