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
        INSERT INTO run (flow_id, device_id, started_at, iterations, mode, source, raw_report_path)
        VALUES (1, 1, '2026-07-22T00:00:00Z', 3, 'warm', 'local:eduardo', NULL)
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
    """`db/migrations/0001_init.sql` (task 1.2) applies cleanly on its own
    and produces the same tables/indexes/view as `schema.sql` — the
    migration is a versioned mirror of the canonical (Rev 2) schema (§9.5).
    Corrected directly in 0001 per decision #40 — no 0002 rename-migration,
    since no DB has ever been deployed with the thin Rev 1 shape."""
    fresh_connection.executescript(MIGRATION_0001.read_text())

    assert EXPECTED_TABLES <= _table_names(fresh_connection)
    assert EXPECTED_INDEXES <= _index_names(fresh_connection)
    assert "run_metric_summary" in _view_names(fresh_connection)


# ===== Rev 2 schema shape (decision #40: corrected directly in 0001) =====

EXPECTED_SYSTEM_SAMPLE_COLUMNS = {
    "iteration_id",
    "total_time_ms",
    "start_time_ms",
    "fps_avg",
    "fps_min",
    "ram_avg_mb",
    "ram_peak_mb",
    "cpu_avg_pct",
    "cpu_peak_pct",
}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _column_info(conn: sqlite3.Connection, table: str, column: str) -> sqlite3.Row:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for row in rows:
        if row[1] == column:
            return row
    raise AssertionError(f"column {column!r} not found on table {table!r}")


@pytest.mark.parametrize("ddl_path", [SCHEMA_SQL, MIGRATION_0001])
def test_system_sample_has_rev2_aggregate_columns(fresh_connection, ddl_path):
    """system_sample carries the full per-iteration aggregate shape (design
    §37/§39): total_time_ms/start_time_ms + fps/ram/cpu avg+min/peak — the
    thin (fps_avg, cpu_pct_avg, ram_mb_avg) Rev 1 shape is gone."""
    fresh_connection.executescript(ddl_path.read_text())

    columns = _column_names(fresh_connection, "system_sample")
    assert columns == EXPECTED_SYSTEM_SAMPLE_COLUMNS
    assert "cpu_pct_avg" not in columns
    assert "ram_mb_avg" not in columns


@pytest.mark.parametrize("ddl_path", [SCHEMA_SQL, MIGRATION_0001])
def test_run_has_raw_report_path_column_nullable(fresh_connection, ddl_path):
    """`run.raw_report_path` references the on-disk Flashlight JSON (one
    report per run) and is nullable — sources without a Flashlight sampler
    persist a run with no report path."""
    fresh_connection.executescript(ddl_path.read_text())

    columns = _column_names(fresh_connection, "run")
    assert "raw_report_path" in columns

    fresh_connection.execute(
        "INSERT INTO device (device_key, model, os_version) VALUES (?, ?, ?)",
        ("Pixel 8 Pro|Android 14|physical", "Pixel 8 Pro", "Android 14"),
    )
    fresh_connection.execute("INSERT INTO flow (name) VALUES (?)", ("prestamos-warm",))
    fresh_connection.execute(
        """
        INSERT INTO run (flow_id, device_id, started_at, iterations, mode, source)
        VALUES (1, 1, '2026-07-22T00:00:00Z', 3, 'warm', 'local:eduardo')
        """
    )
    fresh_connection.commit()

    row = fresh_connection.execute(
        "SELECT raw_report_path FROM run WHERE run_id = 1"
    ).fetchone()
    assert row[0] is None


@pytest.mark.parametrize("ddl_path", [SCHEMA_SQL, MIGRATION_0001])
def test_metric_has_higher_is_better_column_default_zero(fresh_connection, ddl_path):
    """`metric.higher_is_better` carries direction metadata for a future
    COMPARE (decision #39): defaults to 0 (lower-is-better) unless a metric
    is explicitly marked higher-is-better (e.g. fps_avg/fps_min)."""
    fresh_connection.executescript(ddl_path.read_text())

    columns = _column_names(fresh_connection, "metric")
    assert "higher_is_better" in columns

    info = _column_info(fresh_connection, "metric", "higher_is_better")
    # PRAGMA table_info row: (cid, name, type, notnull, dflt_value, pk)
    assert info[3] == 1, "higher_is_better must be NOT NULL"
    assert info[4] == "0", "higher_is_better must DEFAULT 0"

    fresh_connection.execute("INSERT INTO metric (name) VALUES (?)", ("total_time_ms",))
    fresh_connection.commit()
    row = fresh_connection.execute(
        "SELECT higher_is_better FROM metric WHERE name = 'total_time_ms'"
    ).fetchone()
    assert row[0] == 0


@pytest.mark.parametrize("ddl_path", [SCHEMA_SQL, MIGRATION_0001])
def test_run_metric_summary_view_unaffected_by_rev2_columns(fresh_connection, ddl_path):
    """The `run_metric_summary` percentile view is driven by `measure`, not
    `system_sample`/`run`/`metric` — the Rev 2 column additions must not
    change its shape or behavior."""
    fresh_connection.executescript(ddl_path.read_text())

    fresh_connection.execute(
        "INSERT INTO device (device_key, model, os_version) VALUES (?, ?, ?)",
        ("Pixel 8 Pro|Android 14|physical", "Pixel 8 Pro", "Android 14"),
    )
    fresh_connection.execute("INSERT INTO flow (name) VALUES (?)", ("prestamos-warm",))
    fresh_connection.execute(
        "INSERT INTO metric (name, higher_is_better) VALUES (?, ?)", ("fps_avg", 1)
    )
    fresh_connection.execute(
        """
        INSERT INTO run (flow_id, device_id, started_at, iterations, mode, source)
        VALUES (1, 1, '2026-07-22T00:00:00Z', 2, 'warm', 'local:eduardo')
        """
    )
    for duration in (58.0, 60.0):
        fresh_connection.execute(
            "INSERT INTO measure (run_id, metric_id, duration_ms) VALUES (1, 1, ?)",
            (duration,),
        )
    fresh_connection.commit()

    row = fresh_connection.execute(
        "SELECT n, min_ms, max_ms, avg_ms FROM run_metric_summary "
        "WHERE run_id = 1 AND metric_id = 1"
    ).fetchone()
    assert row == (2, 58.0, 60.0, pytest.approx(59.0))


@pytest.mark.parametrize("ddl_path", [SCHEMA_SQL, MIGRATION_0001])
def test_system_sample_still_keyed_by_iteration_pk_fk(fresh_connection, ddl_path):
    """`system_sample.iteration_id` stays the PK/FK to `iteration` — only
    the metric columns changed shape, not the join key."""
    fresh_connection.execute("PRAGMA foreign_keys = ON")
    fresh_connection.executescript(ddl_path.read_text())

    fresh_connection.execute(
        "INSERT INTO device (device_key, model, os_version) VALUES (?, ?, ?)",
        ("Pixel 8 Pro|Android 14|physical", "Pixel 8 Pro", "Android 14"),
    )
    fresh_connection.execute("INSERT INTO flow (name) VALUES (?)", ("prestamos-warm",))
    fresh_connection.execute(
        """
        INSERT INTO run (flow_id, device_id, started_at, iterations, mode, source)
        VALUES (1, 1, '2026-07-22T00:00:00Z', 1, 'warm', 'local:eduardo')
        """
    )
    fresh_connection.execute(
        "INSERT INTO iteration (run_id, idx) VALUES (1, 0)"
    )
    fresh_connection.execute(
        """
        INSERT INTO system_sample (
            iteration_id, total_time_ms, start_time_ms,
            fps_avg, fps_min, ram_avg_mb, ram_peak_mb, cpu_avg_pct, cpu_peak_pct
        ) VALUES (1, 46712.0, 1342.0, 59.28, 55.0, 210.5, 240.0, 12.4, 30.0)
        """
    )
    fresh_connection.commit()

    row = fresh_connection.execute(
        "SELECT iteration_id, total_time_ms, fps_min FROM system_sample WHERE iteration_id = 1"
    ).fetchone()
    assert row == (1, 46712.0, 55.0)

    with pytest.raises(sqlite3.IntegrityError):
        fresh_connection.execute(
            """
            INSERT INTO system_sample (iteration_id, total_time_ms)
            VALUES (999, 1.0)
            """
        )
