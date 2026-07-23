"""§9.6 single-transaction ingestion tests for `SqliteStore.save_run`.

RED-before-GREEN: written before `src/perf/adapters/store_sqlite.py`
existed. Exercises the highest-risk paths named in the PR2 apply
instructions — full rollback on any exception (a crashed run leaves ZERO
rows across dimensions AND facts, since dimension upserts happen inside
the same transaction), dimension-upsert idempotency across runs, and
SQL-injection safety for user-supplied names (device/flow/metric/marker).
Drives `save_run` purely through domain value objects + a fake `Clock` —
no adapters, no real device/Flashlight/adb.
"""

from __future__ import annotations

import sqlite3

import pytest

from perf.adapters.store_sqlite import SqliteStore
from perf.domain.model import Marker, RunContext, SystemSample


class _FrozenClock:
    """Fake `Clock` port (design §"Key ports") — deterministic `started_at`."""

    def __init__(self, iso: str = "2026-07-22T00:00:00+00:00") -> None:
        self._iso = iso

    def now_utc_iso(self) -> str:
        return self._iso


def _run_context(**overrides) -> RunContext:
    defaults = {
        "device_key": "Pixel 8 Pro|Android 14|physical",
        "model": "Pixel 8 Pro",
        "os_version": "Android 14",
        "is_emulator": False,
        "source": "local:eduardo",
        "git_commit": "abc123",
        "git_branch": "main",
        "app_version": "1.2.3",
        "is_dev_bundle": False,
        "bundle_source": "embedded",
        "build_variant": "release",
        "tool_version": "0.1.0",
    }
    defaults.update(overrides)
    return RunContext(**defaults)


@pytest.fixture()
def store(tmp_path):
    db_path = tmp_path / "perf.db"
    s = SqliteStore(db_path, clock=_FrozenClock())
    try:
        yield s
    finally:
        s.close()


_FACT_AND_DIMENSION_TABLES = (
    "run",
    "iteration",
    "measure",
    "system_sample",
    "device",
    "flow",
    "metric",
)


def _row_counts(conn: sqlite3.Connection) -> dict:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in _FACT_AND_DIMENSION_TABLES
    }


def test_successful_save_run_persists_exactly_one_run_and_its_facts(store):
    ctx = _run_context()
    markers = [Marker(name="checkout", value=900.0, unit="ms")]
    samples = [
        SystemSample(
            iteration_idx=0,
            total_time_ms=46712.0,
            start_time_ms=1342.0,
            fps_avg=59.28,
            fps_min=55.0,
            ram_avg_mb=210.5,
            ram_peak_mb=240.0,
            cpu_avg_pct=12.4,
            cpu_peak_pct=30.0,
        ),
        SystemSample(
            iteration_idx=1,
            total_time_ms=45000.0,
            start_time_ms=1200.0,
            fps_avg=58.0,
            fps_min=50.0,
            ram_avg_mb=200.0,
            ram_peak_mb=230.0,
            cpu_avg_pct=11.0,
            cpu_peak_pct=28.0,
        ),
    ]

    run_id = store.save_run(
        ctx, "prestamos-warm", 2, "warm", "local:eduardo", markers, samples, "results/run1.json"
    )

    counts = _row_counts(store._conn)
    assert run_id == 1
    assert counts["run"] == 1
    assert counts["iteration"] == 2
    assert counts["system_sample"] == 2
    assert counts["measure"] == 1
    assert counts["device"] == 1
    assert counts["flow"] == 1
    assert counts["metric"] == 9  # "checkout" marker + 8 system-sample aggregate names

    summary = store.get_run_summary(run_id)
    assert summary["flow_name"] == "prestamos-warm"
    assert summary["device_key"] == ctx.device_key
    assert summary["started_at"] == "2026-07-22T00:00:00+00:00"
    assert summary["raw_report_path"] == "results/run1.json"
    assert summary["iterations_captured"] == 2
    assert summary["measures_captured"] == 1
    assert summary["is_dev_bundle"] is False


def test_marker_unit_is_persisted_not_defaulted_to_ms(store):
    """Regression (PR3 verify, WARNING-2): a marker with a non-`ms` unit must
    persist that unit on the `metric` dimension, not silently default to
    `'ms'` (a COMPARE reading `metric.unit` would otherwise misinterpret it)."""
    ctx = _run_context()
    markers = [Marker(name="startup", value=1.5, unit="s")]

    store.save_run(ctx, "prestamos-warm", 1, "warm", "local:eduardo", markers, [], None)

    unit = store._conn.execute("SELECT unit FROM metric WHERE name = 'startup'").fetchone()[0]
    assert unit == "s"


def test_save_run_raising_partway_leaves_zero_rows_full_rollback(store):
    """A crashed run leaves ZERO rows — dimension upserts happen inside the
    SAME transaction as the facts, so they roll back too (§9.6: "roll back
    the whole run on ANY exception ... never leave half-written history")."""
    ctx = _run_context()
    markers = [Marker(name="checkout", value=900.0, unit="ms")]
    # Duplicate iteration_idx (0, 0) violates the UNIQUE (run_id, idx)
    # constraint on the second `iteration` insert — simulates a crash
    # partway through fact insertion, after dimension upserts already ran.
    samples = [
        SystemSample(0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        SystemSample(0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0),
    ]

    with pytest.raises(sqlite3.IntegrityError):
        store.save_run(ctx, "prestamos-warm", 2, "warm", "local:eduardo", markers, samples, None)

    counts = _row_counts(store._conn)
    assert all(count == 0 for count in counts.values()), counts

    # the store must remain usable after a rolled-back transaction
    run_id = store.save_run(ctx, "prestamos-warm", 1, "warm", "local:eduardo", [], [], None)
    assert run_id == 1


def test_dimension_upserts_are_idempotent_across_successful_runs(store):
    ctx = _run_context()
    markers = [Marker(name="checkout", value=900.0, unit="ms")]
    samples = [SystemSample(0, 100.0, 10.0, 60.0, 55.0, 100.0, 110.0, 10.0, 15.0)]

    run_id_1 = store.save_run(
        ctx, "prestamos-warm", 1, "warm", "local:eduardo", markers, samples, None
    )
    run_id_2 = store.save_run(
        ctx, "prestamos-warm", 1, "warm", "local:eduardo", markers, samples, None
    )

    assert run_id_1 != run_id_2
    counts = _row_counts(store._conn)
    assert counts["device"] == 1
    assert counts["flow"] == 1
    assert counts["metric"] == len(
        {
            "checkout",
            "total_time_ms",
            "start_time_ms",
            "fps_avg",
            "fps_min",
            "ram_avg_mb",
            "ram_peak_mb",
            "cpu_avg_pct",
            "cpu_peak_pct",
        }
    )
    assert counts["run"] == 2  # facts DO grow — only dimensions are deduped


def test_metric_direction_metadata_recorded_correctly(store):
    ctx = _run_context()
    samples = [SystemSample(0, 46712.0, 1342.0, 59.28, 55.0, 210.5, 240.0, 12.4, 30.0)]

    store.save_run(ctx, "prestamos-warm", 1, "warm", "local:eduardo", [], samples, None)

    rows = dict(store._conn.execute("SELECT name, higher_is_better FROM metric"))
    assert rows["fps_avg"] == 1
    assert rows["fps_min"] == 1
    assert rows["total_time_ms"] == 0
    assert rows["ram_peak_mb"] == 0
    assert rows["cpu_avg_pct"] == 0


def test_sql_metacharacter_names_round_trip_safely_as_bound_values(store):
    """A device/flow/metric NAME containing SQL metacharacters is stored
    and queried safely as a VALUE (parameterized) — never as an
    interpolated identifier. If this were vulnerable, the literal
    `DROP TABLE run` text would have actually dropped the table."""
    malicious_name = "'; DROP TABLE run;--"
    ctx = _run_context(device_key=malicious_name)
    markers = [Marker(name=malicious_name, value=1.0, unit="ms")]

    run_id = store.save_run(ctx, malicious_name, 0, "warm", "local:eduardo", markers, [], None)

    tables = {
        row[0] for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "run" in tables  # table survives — proves no injection occurred

    device_row = store._conn.execute(
        "SELECT device_key FROM device WHERE device_key = ?", (malicious_name,)
    ).fetchone()
    assert device_row[0] == malicious_name

    flow_row = store._conn.execute(
        "SELECT name FROM flow WHERE name = ?", (malicious_name,)
    ).fetchone()
    assert flow_row[0] == malicious_name

    metric_row = store._conn.execute(
        "SELECT name FROM metric WHERE name = ?", (malicious_name,)
    ).fetchone()
    assert metric_row[0] == malicious_name

    summary = store.get_run_summary(run_id)
    assert summary["flow_name"] == malicious_name
