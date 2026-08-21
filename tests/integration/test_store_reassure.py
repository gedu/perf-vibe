"""Integration tests for `SqliteStore.save_reassure_import` (design
"Transaction Boundary — `adapters/store_sqlite.py`", PR3).

Drives the store purely through the domain value objects
(`ReassureHeader`/`ReassureEntry`/`ReassureParseResult`) + a fake `Clock` —
no parser, no real `.perf` file. The tables already exist on `main` (PR1);
this module is the persistence-layer twin of
`tests/integration/test_reassure_jsonl.py`'s parse-layer non-alignment
guard.

RED-before-GREEN: written before `SqliteStore.save_reassure_import` existed.
The load-bearing test (`test_non_alignment_load_bearing_...`) is the
persistence-layer regression guard for the entire feature: `durations[]`
and `counts[]` MUST be persisted via TWO INDEPENDENT insert loops, never
one zipped loop — the whole reason `reassure_duration_sample` and
`reassure_count_sample` are separate tables.
"""

from __future__ import annotations

import sqlite3

import pytest

from fakes import FrozenClock
from perf.adapters.store_sqlite import SqliteStore
from perf.domain.model import ReassureEntry, ReassureHeader, ReassureParseResult

_REASSURE_TABLES = (
    "reassure_import",
    "reassure_entry",
    "reassure_duration_sample",
    "reassure_count_sample",
)


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in _REASSURE_TABLES
    }


def _entry(**overrides: object) -> ReassureEntry:
    defaults: dict[str, object] = {
        "name": "WidgetPanel Performance Tests WidgetPanel renders correctly",
        "entry_type": "render",
        "runs": 8,
        "durations": (10.1, 10.2, 10.3, 10.4, 10.5, 10.6),
        "counts": (1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 1.0, 1.0),
        "warmup_durations_json": None,
        "outlier_durations_json": None,
    }
    defaults.update(overrides)
    return ReassureEntry(**defaults)  # type: ignore[arg-type]


def _result(
    *,
    entries: tuple[ReassureEntry, ...],
    content_hash: str = "deadbeef" * 8,
    header: ReassureHeader | None = None,
    skipped: tuple[tuple[int, str], ...] = (),
) -> ReassureParseResult:
    return ReassureParseResult(
        header=header,
        entries=entries,
        content_hash=content_hash,
        skipped=skipped,
        partial_coverage=bool(skipped),
        diagnostic=None,
    )


@pytest.fixture()
def store(tmp_path):
    db_path = tmp_path / "perf.db"
    s = SqliteStore(db_path, clock=FrozenClock("2026-08-20T00:00:00+00:00"))
    try:
        yield s
    finally:
        s.close()


def test_non_alignment_load_bearing_persists_two_independent_series(store):
    """[load-bearing] An 8-count/6-duration entry persists exactly 6
    `reassure_duration_sample` rows and 8 `reassure_count_sample` rows, each
    with contiguous `idx` from 0 WITHIN ITS OWN TABLE — no padding, no
    truncation, no cross-series pairing."""
    entry = _entry()
    result = _result(entries=(entry,))

    import_id = store.save_reassure_import(result, "path/to/current.perf", "current")

    assert import_id is not None
    counts = _row_counts(store._conn)
    assert counts["reassure_duration_sample"] == 6
    assert counts["reassure_count_sample"] == 8

    duration_idx = [
        row[0]
        for row in store._conn.execute(
            "SELECT idx FROM reassure_duration_sample ORDER BY idx"
        ).fetchall()
    ]
    count_idx = [
        row[0]
        for row in store._conn.execute(
            "SELECT idx FROM reassure_count_sample ORDER BY idx"
        ).fetchall()
    ]
    assert duration_idx == list(range(6))
    assert count_idx == list(range(8))

    duration_values = [
        row[0]
        for row in store._conn.execute(
            "SELECT duration_ms FROM reassure_duration_sample ORDER BY idx"
        ).fetchall()
    ]
    count_values = [
        row[0]
        for row in store._conn.execute(
            "SELECT render_count FROM reassure_count_sample ORDER BY idx"
        ).fetchall()
    ]
    assert duration_values == list(entry.durations)
    assert count_values == list(entry.counts)


def test_runs_persisted_verbatim_never_reconciled_against_actual_counts(store):
    """`runs` is stored as DECLARED, even when it disagrees with the actual
    number of persisted `counts` rows — never repaired, never a skip."""
    entry = _entry(name="mismatched runs", runs=10, durations=(1.0,), counts=(1.0, 2.0, 3.0))
    result = _result(entries=(entry,))

    store.save_reassure_import(result, "path/to/current.perf", "current")

    row = store._conn.execute(
        "SELECT runs FROM reassure_entry WHERE name = ?", ("mismatched runs",)
    ).fetchone()
    assert row[0] == 10
    count_rows = store._conn.execute("SELECT COUNT(*) FROM reassure_count_sample").fetchone()[0]
    assert count_rows == 3


def test_empty_durations_persists_entry_with_zero_duration_rows(store):
    """An entry with `durations: []` (every post-warmup run classified an
    outlier) is persisted with zero duration samples — never skipped —
    while its `counts` series remains valid data."""
    entry = _entry(name="every run an outlier", runs=3, durations=(), counts=(4.0, 5.0, 6.0))
    result = _result(entries=(entry,))

    store.save_reassure_import(result, "path/to/current.perf", "current")

    duration_rows = store._conn.execute("SELECT COUNT(*) FROM reassure_duration_sample").fetchone()[
        0
    ]
    count_rows = store._conn.execute("SELECT COUNT(*) FROM reassure_count_sample").fetchone()[0]
    entries = store._conn.execute("SELECT COUNT(*) FROM reassure_entry").fetchone()[0]
    assert duration_rows == 0
    assert count_rows == 3
    assert entries == 1


def test_warmup_and_outlier_durations_absent_persist_as_sql_null(store):
    """`warmup_durations`/`outlier_durations` persist as SQL `NULL` when the
    JSON key was absent on the domain type (both `None`)."""
    entry = _entry(warmup_durations_json=None, outlier_durations_json=None)
    result = _result(entries=(entry,))

    store.save_reassure_import(result, "path/to/current.perf", "current")

    row = store._conn.execute(
        "SELECT warmup_durations, outlier_durations FROM reassure_entry"
    ).fetchone()
    assert row[0] is None
    assert row[1] is None


def test_warmup_and_outlier_durations_present_empty_persist_as_literal_bracket_pair(store):
    """`warmup_durations`/`outlier_durations` persist as the literal `'[]'`
    string when the JSON key was present but empty — NOT collapsed with the
    absent (`NULL`) case."""
    entry = _entry(warmup_durations_json="[]", outlier_durations_json="[]")
    result = _result(entries=(entry,))

    store.save_reassure_import(result, "path/to/current.perf", "current")

    row = store._conn.execute(
        "SELECT warmup_durations, outlier_durations FROM reassure_entry"
    ).fetchone()
    assert row[0] == "[]"
    assert row[1] == "[]"


def test_issues_diagnostics_persist_with_their_real_observed_types(store):
    """`issues.initialUpdateCount` (INTEGER) and `issues.redundantUpdates`
    (verbatim JSON ARRAY passthrough) land in the `0007` columns inside the
    same single transaction as everything else."""
    entry = _entry(initial_update_count=1, redundant_updates_json="[1, 2, 3]")
    result = _result(entries=(entry,))

    store.save_reassure_import(result, "path/to/current.perf", "current")

    row = store._conn.execute(
        "SELECT issues_initial_update_count, issues_redundant_updates FROM reassure_entry"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == "[1, 2, 3]"


def test_issues_absent_persists_as_sql_null_while_zero_persists_as_zero(store):
    """The load-bearing distinction, at rest: `None` (`issues` was absent)
    persists as SQL `NULL`, while a present `0` persists as `0` and a present
    empty array persists as the literal `'[]'`. Collapsing either pair would
    turn "we never measured this" into "we measured it and found nothing"."""
    absent = _entry(name="issues absent", initial_update_count=None, redundant_updates_json=None)
    present_zero = _entry(
        name="issues present and zero", initial_update_count=0, redundant_updates_json="[]"
    )
    result = _result(entries=(absent, present_zero))

    store.save_reassure_import(result, "path/to/current.perf", "current")

    rows = store._conn.execute(
        "SELECT issues_initial_update_count, issues_redundant_updates "
        "FROM reassure_entry ORDER BY entry_id"
    ).fetchall()
    assert rows[0] == (None, None)
    assert rows[1] == (0, "[]")
    # A NULL and a 0 must remain separable by SQL, not merely by Python.
    null_count = store._conn.execute(
        "SELECT COUNT(*) FROM reassure_entry WHERE issues_initial_update_count IS NULL"
    ).fetchone()[0]
    assert null_count == 1


def test_duplicate_byte_identical_import_returns_none_and_inserts_zero_rows(store):
    """A byte-identical re-import (same `content_hash`) inserts ZERO rows
    across ALL FOUR tables and returns `None` — `rowcount == 0` after
    `ON CONFLICT(content_hash) DO NOTHING`, never a pre-`SELECT`, never
    `lastrowid` (which would be stale on a no-op insert)."""
    entry = _entry()
    result = _result(entries=(entry,), content_hash="samehash" * 8)

    first_id = store.save_reassure_import(result, "path/to/current.perf", "current")
    assert first_id is not None
    before = _row_counts(store._conn)

    second_id = store.save_reassure_import(result, "path/to/current.perf", "current")

    assert second_id is None
    after = _row_counts(store._conn)
    assert after == before


def test_zero_entries_still_commits_one_import_row_with_no_entry_or_sample_rows(store):
    """R3 Reliability review finding on PR3 (lineage `review-1fc710595e9babbb`,
    non-blocking SUGGESTION): only an entry with EMPTY `durations` was
    covered before, which proves the inner per-entry sample loops handle a
    zero-length SERIES but never proved that a `ReassureParseResult` with
    ZERO ENTRIES still commits at all. The spec requires "zero entries
    recovered from a readable file -> exit 0 with a payload flag" (PR4b's
    CLI hits this path directly), so the store must accept `entries=()` as
    a normal outcome: one `reassure_import` row, a real `import_id`, and
    nothing in the three entry/sample tables — never `None`, never a
    rolled-back transaction."""
    result = _result(entries=())

    import_id = store.save_reassure_import(result, "path/to/current.perf", "current")

    assert import_id is not None
    counts = _row_counts(store._conn)
    assert counts["reassure_import"] == 1
    assert counts["reassure_entry"] == 0
    assert counts["reassure_duration_sample"] == 0
    assert counts["reassure_count_sample"] == 0


def test_source_path_stored_in_source_path_column(store):
    entry = _entry()
    result = _result(entries=(entry,))

    store.save_reassure_import(result, "reports/current.perf", "current")

    row = store._conn.execute("SELECT source_path FROM reassure_import").fetchone()
    assert row[0] == "reports/current.perf"


def test_imported_at_comes_from_injected_clock(store):
    entry = _entry()
    result = _result(entries=(entry,))

    store.save_reassure_import(result, "path/to/current.perf", "current")

    row = store._conn.execute("SELECT imported_at FROM reassure_import").fetchone()
    assert row[0] == "2026-08-20T00:00:00+00:00"


def test_header_metadata_persisted_when_present(store):
    header = ReassureHeader(branch="main", commit_hash="abc123", created_date="2026-01-01")
    entry = _entry()
    result = _result(entries=(entry,), header=header)

    store.save_reassure_import(result, "path/to/current.perf", "current")

    row = store._conn.execute(
        "SELECT branch, commit_hash, created_date FROM reassure_import"
    ).fetchone()
    assert row == ("main", "abc123", "2026-01-01")


def test_header_absent_persists_null_metadata_columns(store):
    entry = _entry()
    result = _result(entries=(entry,), header=None)

    store.save_reassure_import(result, "path/to/current.perf", "current")

    row = store._conn.execute(
        "SELECT branch, commit_hash, created_date FROM reassure_import"
    ).fetchone()
    assert row == (None, None, None)


def test_mid_transaction_failure_rolls_back_leaving_zero_rows_in_all_four_tables(store):
    """A failure part-way through the transaction leaves ZERO rows in ALL
    FOUR tables — full rollback, matching `save_run`'s
    `except Exception: ROLLBACK; raise` shape.

    The failure is a REAL constraint violation from the real insert, not a
    patched internal: `ReassureEntry` is a plain frozen dataclass with no
    validation, so an entry carrying `name=None` reaches
    `name TEXT NOT NULL` and SQLite raises `IntegrityError`. Monkeypatching
    `SqliteStore`'s own insert helper would test a fake instead of the real
    wiring (`python-testing` rule 3), and would prove only that a
    hand-thrown exception rolls back — never that a genuine database error
    does.

    The bad entry is SECOND so the first entry's already-inserted rows must
    roll back too, not merely be absent.
    """
    good_entry = _entry(name="first entry")
    bad_entry = _entry(name=None)  # violates `name TEXT NOT NULL`
    result = _result(entries=(good_entry, bad_entry))

    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        store.save_reassure_import(result, "path/to/current.perf", "current")

    counts = _row_counts(store._conn)
    assert all(count == 0 for count in counts.values()), counts

    # The store must remain usable after a rolled-back transaction, and the
    # rolled-back `content_hash` must NOT have been consumed as a duplicate.
    reimport_id = store.save_reassure_import(
        _result(entries=(good_entry,)), "path/to/current.perf", "current"
    )
    assert reimport_id is not None
    assert _row_counts(store._conn)["reassure_import"] == 1


def test_kind_persisted_verbatim_for_each_allowed_value(store):
    """`kind` (PR4a's `0006_add_reassure_import_kind.sql` column, unwritten
    until this slice) is persisted verbatim for each of the three allowed
    values — no rewriting, no normalization."""
    for kind, content_hash in (
        ("current", "current-hash" * 5),
        ("baseline", "baseline-hash" * 5),
        ("unknown", "unknown-hash" * 5),
    ):
        entry = _entry(name=f"{kind} entry")
        result = _result(entries=(entry,), content_hash=content_hash)

        import_id = store.save_reassure_import(result, "path/to/file.perf", kind)

        assert import_id is not None
        row = store._conn.execute(
            "SELECT kind FROM reassure_import WHERE import_id = ?", (import_id,)
        ).fetchone()
        assert row[0] == kind


def test_invalid_kind_raises_value_error_before_any_row_is_written(store):
    """The store validates `kind` at the adapter boundary (the schema
    deliberately carries no `CHECK` constraint — house style, matches
    `run.mode`/`reassure_entry.entry_type`). An invalid value raises
    `ValueError` before `BEGIN`, leaving the transaction untouched — not a
    rolled-back partial write, no write attempted at all."""
    entry = _entry()
    result = _result(entries=(entry,))

    with pytest.raises(ValueError, match="kind"):
        store.save_reassure_import(result, "path/to/current.perf", "nightly")

    counts = _row_counts(store._conn)
    assert all(count == 0 for count in counts.values()), counts
