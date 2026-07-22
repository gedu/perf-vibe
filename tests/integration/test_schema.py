"""DB-schema smoke tests (PR1 scope only).

Verifies `db/schema.sql` and `db/migrations/0001_init.sql` apply cleanly to
a fresh temp SQLite database and that `run_metric_summary` (§9.3) is
creatable and queryable. The migration RUNNER (PRAGMA user_version bump,
ordered-file application, WAL/busy_timeout pragmas, ingestion transaction)
is `adapters/store_sqlite.py` — that is PR2 scope and is intentionally NOT
tested here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

DB_DIR = Path(__file__).resolve().parents[2] / "src" / "perf" / "db"
SCHEMA_SQL = DB_DIR / "schema.sql"
MIGRATION_0001 = DB_DIR / "migrations" / "0001_init.sql"

EXPECTED_TABLES = {"device", "flow", "metric", "run", "iteration", "measure", "system_sample"}
EXPECTED_INDEXES = {"idx_run_flow_device_time", "idx_measure_metric", "idx_measure_run"}


@pytest.fixture()
def fresh_connection(tmp_path):
    db_path = tmp_path / "perf-test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        yield conn
    finally:
        conn.close()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {row[0] for row in rows}


def _view_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall()
    return {row[0] for row in rows}


def test_schema_sql_applies_cleanly_and_creates_expected_tables(fresh_connection):
    fresh_connection.executescript(SCHEMA_SQL.read_text())

    assert EXPECTED_TABLES <= _table_names(fresh_connection)
    assert EXPECTED_INDEXES <= _index_names(fresh_connection)
    assert "run_metric_summary" in _view_names(fresh_connection)


def test_run_metric_summary_view_is_queryable_and_computes_percentiles(fresh_connection):
    fresh_connection.executescript(SCHEMA_SQL.read_text())

    fresh_connection.execute(
        "INSERT INTO device (device_key, model, os_version) VALUES (?, ?, ?)",
        ("Pixel 8 Pro|Android 14|physical", "Pixel 8 Pro", "Android 14"),
    )
    fresh_connection.execute("INSERT INTO flow (name) VALUES (?)", ("prestamos-warm",))
    fresh_connection.execute("INSERT INTO metric (name) VALUES (?)", ("/loans/details/:id",))
    fresh_connection.execute(
        """
        INSERT INTO run (flow_id, device_id, started_at, iterations, mode, source)
        VALUES (1, 1, '2026-07-22T00:00:00Z', 3, 'warm', 'local:eduardo')
        """
    )
    for duration in (900.0, 950.0, 1000.0):
        fresh_connection.execute(
            "INSERT INTO measure (run_id, metric_id, duration_ms) VALUES (1, 1, ?)",
            (duration,),
        )
    fresh_connection.commit()

    row = fresh_connection.execute(
        "SELECT n, min_ms, max_ms, avg_ms, p50_ms, p90_ms FROM run_metric_summary "
        "WHERE run_id = 1 AND metric_id = 1"
    ).fetchone()

    assert row is not None
    n, min_ms, max_ms, avg_ms, p50_ms, p90_ms = row
    assert n == 3
    assert min_ms == 900.0
    assert max_ms == 1000.0
    assert avg_ms == pytest.approx(950.0)
    assert p50_ms == 950.0


def test_dimension_upserts_are_idempotent_via_unique_constraint(fresh_connection):
    """Guards the §9.2 UNIQUE constraints that the PR2 ingestion transaction
    relies on for `INSERT ... ON CONFLICT` idempotency — repeated
    device/flow/metric names must not be insertable as duplicate rows."""
    fresh_connection.executescript(SCHEMA_SQL.read_text())

    fresh_connection.execute(
        "INSERT INTO device (device_key, model, os_version) VALUES (?, ?, ?)",
        ("Pixel 8 Pro|Android 14|physical", "Pixel 8 Pro", "Android 14"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        fresh_connection.execute(
            "INSERT INTO device (device_key, model, os_version) VALUES (?, ?, ?)",
            ("Pixel 8 Pro|Android 14|physical", "Pixel 8 Pro", "Android 14"),
        )


def test_foreign_keys_pragma_enforced_when_enabled(fresh_connection):
    fresh_connection.execute("PRAGMA foreign_keys = ON")
    fresh_connection.executescript(SCHEMA_SQL.read_text())

    with pytest.raises(sqlite3.IntegrityError):
        fresh_connection.execute(
            """
            INSERT INTO run (flow_id, device_id, started_at, iterations, mode, source)
            VALUES (999, 999, '2026-07-22T00:00:00Z', 1, 'warm', 'local:eduardo')
            """
        )


def test_migration_0001_matches_schema_ddl(fresh_connection):
    """`db/migrations/0001_init.sql` (task 3.2) applies cleanly on its own
    and produces the same tables/indexes/view as `schema.sql` — the
    migration is a versioned mirror of the canonical schema (§9.5)."""
    fresh_connection.executescript(MIGRATION_0001.read_text())

    assert EXPECTED_TABLES <= _table_names(fresh_connection)
    assert EXPECTED_INDEXES <= _index_names(fresh_connection)
    assert "run_metric_summary" in _view_names(fresh_connection)
