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
MIGRATION_0002 = DB_DIR / "migrations" / "0002_compare_baseline_index.sql"
MIGRATION_0003 = DB_DIR / "migrations" / "0003_fix_p90_ceil_rank.sql"
# 0004_fix_system_sample_units.sql is data-only (no DDL) and is intentionally
# absent here — only DDL migrations join the equivalence test below.
MIGRATION_0005 = DB_DIR / "migrations" / "0005_add_reassure_tables.sql"
MIGRATION_0006 = DB_DIR / "migrations" / "0006_add_reassure_import_kind.sql"

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

    assert _table_names(fresh_connection) >= EXPECTED_TABLES
    assert _index_names(fresh_connection) >= EXPECTED_INDEXES
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
    n, min_ms, max_ms, avg_ms, p50_ms, _p90_ms = row
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
    and produces the tables/indexes/view of the (Rev 2) schema (§9.5).
    Corrected directly in 0001 per decision #40 — no 0002 rename-migration,
    since no DB has ever been deployed with the thin Rev 1 shape. `0001`
    alone is intentionally missing the Rev 3 `idx_run_baseline` index
    (added by `0002_compare_baseline_index.sql` — see
    `test_schema_sql_and_migrations_are_fully_equivalent` below)."""
    fresh_connection.executescript(MIGRATION_0001.read_text())

    assert _table_names(fresh_connection) >= EXPECTED_TABLES
    assert _index_names(fresh_connection) >= EXPECTED_INDEXES
    assert "run_metric_summary" in _view_names(fresh_connection)


def _introspect_full_schema(conn: sqlite3.Connection) -> dict:
    """Full comparable schema across EVERY table: per-table columns
    (name, type, NOT NULL, default, PK) and foreign keys, plus index and view
    definitions. Table names come from sqlite_master (our own schema, not user
    input), so interpolating them into PRAGMA is safe here."""
    schema: dict = {}
    tables = sorted(
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    for table in tables:
        # PRAGMA table_info row: (cid, name, type, notnull, dflt_value, pk) — drop cid (ordinal).
        columns = [tuple(row[1:]) for row in conn.execute(f"PRAGMA table_info({table})")]
        foreign_keys = sorted(
            tuple(row) for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        )
        schema[table] = {"columns": columns, "foreign_keys": foreign_keys}
    schema["__indexes_and_views__"] = sorted(
        (row[0], row[1], row[2])
        for row in conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('index','view')"
        )
    )
    return schema


def test_schema_sql_and_migrations_are_fully_equivalent():
    """Strong drift guard (hardens the earlier subset check): applying
    schema.sql on one fresh DB, and applying EVERY DDL migration in order
    (0001, 0002, 0003, 0005, 0006 -- 0004 is data-only, see above) on
    another, must yield IDENTICAL schemas across ALL tables — columns,
    types, NOT NULL, defaults, PK, foreign keys — plus indexes and views.
    A one-sided edit to ANY table/column, or a missing/extra index (e.g.
    forgetting to mirror `idx_run_baseline` into `schema.sql`), fails
    this test (design 'Additive migration': 'fresh and migrated DBs
    converge')."""
    conn_schema = sqlite3.connect(":memory:")
    conn_migration = sqlite3.connect(":memory:")
    try:
        conn_schema.executescript(SCHEMA_SQL.read_text())
        conn_migration.executescript(MIGRATION_0001.read_text())
        conn_migration.executescript(MIGRATION_0002.read_text())
        conn_migration.executescript(MIGRATION_0003.read_text())
        conn_migration.executescript(MIGRATION_0005.read_text())
        conn_migration.executescript(MIGRATION_0006.read_text())
        assert _introspect_full_schema(conn_schema) == _introspect_full_schema(conn_migration)
    finally:
        conn_schema.close()
        conn_migration.close()


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

    row = fresh_connection.execute("SELECT raw_report_path FROM run WHERE run_id = 1").fetchone()
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
    fresh_connection.execute("INSERT INTO iteration (run_id, idx) VALUES (1, 0)")
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


# ===== p90_ms CEIL nearest-rank fix (math / anti-false-positive, Task 1):
# the view's p90_ms MUST equal `domain.statistics.percentile(values, 90)`
# (CEIL nearest-rank) for every n — the earlier floor form was optimistically
# biased (n=2 returned the MINIMUM as p90). =====


def _seed_single_metric_run(conn: sqlite3.Connection, durations) -> None:
    """Seed ONE run with one metric whose `measure` rows carry `durations`,
    so `run_metric_summary` computes over exactly those values."""
    conn.execute(
        "INSERT INTO device (device_key, model, os_version) VALUES (?, ?, ?)",
        ("Pixel 8 Pro|Android 14|physical", "Pixel 8 Pro", "Android 14"),
    )
    conn.execute("INSERT INTO flow (name) VALUES (?)", ("checkout",))
    conn.execute("INSERT INTO metric (name) VALUES (?)", ("checkout",))
    conn.execute(
        """
        INSERT INTO run (flow_id, device_id, started_at, iterations, mode, source)
        VALUES (1, 1, '2026-07-22T00:00:00Z', 1, 'warm', 'local:eduardo')
        """
    )
    for duration in durations:
        conn.execute(
            "INSERT INTO measure (run_id, metric_id, duration_ms) VALUES (1, 1, ?)",
            (float(duration),),
        )
    conn.commit()


def _view_p90(conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        "SELECT p90_ms FROM run_metric_summary WHERE run_id = 1 AND metric_id = 1"
    ).fetchone()
    return None if row is None else row[0]


@pytest.mark.parametrize("ddl_path", [SCHEMA_SQL])
@pytest.mark.parametrize("n", list(range(1, 31)))
def test_view_p90_matches_domain_percentile_ceil_nearest_rank(fresh_connection, ddl_path, n):
    """Property-style: for values 10, 20, ..., 10n the view's `p90_ms` must
    equal `domain.statistics.percentile(values, 90)` (CEIL nearest-rank) for
    every n in 1..30 — the SQL and the domain now agree on the SAME
    convention (the domain docstring's long-standing claim is finally true)."""
    from perf.domain.statistics import percentile

    fresh_connection.executescript(ddl_path.read_text())
    values = [10.0 * (i + 1) for i in range(n)]
    _seed_single_metric_run(fresh_connection, values)

    assert _view_p90(fresh_connection) == percentile(values, 90)


def test_view_p90_n2_is_the_max_never_the_min(fresh_connection):
    """Explicit n=2 regression pin: floor nearest-rank returned the MINIMUM
    as p90 (rank floor(1.8)=1) — a systematic optimistic bias. CEIL
    nearest-rank (rank ceil(1.8)=2) returns the MAX."""
    fresh_connection.executescript(SCHEMA_SQL.read_text())
    _seed_single_metric_run(fresh_connection, [100.0, 900.0])

    p90 = _view_p90(fresh_connection)
    assert p90 == 900.0  # the MAX
    assert p90 != 100.0  # never the MIN (the old floor bug)


def test_view_p90_n1_returns_the_single_value_not_null(fresh_connection):
    """CEIL nearest-rank makes n=1 well-defined: p90 of a single-measure run
    is that value (rank ceil(0.9)=1), NOT NULL. The old floor form returned
    NULL for n=1 (`CAST(0.9*1 AS INT)`=0, nothing qualified) — a symptom of
    the same bug, now gone."""
    fresh_connection.executescript(SCHEMA_SQL.read_text())
    _seed_single_metric_run(fresh_connection, [42.0])

    assert _view_p90(fresh_connection) == 42.0


def test_no_reassure_table_stores_a_component_or_test_file_dimension(fresh_connection):
    """Load-bearing guard for spec requirement "No Component or Test-File
    Identity". The reassure `.perf` format has NO component field and NO
    test-file field: `name` is the sole identity, and any grouping is DERIVED
    from a project naming convention at read time.

    This was previously satisfied only by construction — nobody had added
    such a column, so nothing failed. That is exactly how the sibling
    invariant (`durations`/`counts` not index-aligned) would have been lost
    too, and that one got guards at three layers while this one had none.
    The schema/migration equivalence test does NOT cover it: adding a
    `component` column to BOTH `schema.sql` and a migration is one-sided
    drift only in the sense that both sides agree, so it would pass.

    Storing a component would turn a read-time convention into persisted
    data that goes stale the moment a project renames a `describe` block.
    """
    fresh_connection.executescript(SCHEMA_SQL.read_text())

    reassure_tables = [
        row[0]
        for row in fresh_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'reassure%'"
        )
    ]
    assert reassure_tables, "expected the reassure tables to exist in schema.sql"

    forbidden = ("component", "test_file", "testfile", "suite", "describe", "screen")
    for table in reassure_tables:
        columns = [
            row[1].lower() for row in fresh_connection.execute(f"PRAGMA table_info({table})")
        ]
        for column in columns:
            for token in forbidden:
                assert token not in column, (
                    f"{table}.{column} looks like a stored component/test-file dimension; "
                    "the format has neither field, so any grouping must stay DERIVED"
                )
