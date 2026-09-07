"""Integration tests for the PR1a read-path `Store` methods —
`reassure_imports`/`reassure_entries` (design "Read Models", "Ports",
"Query budget" — this repo files store/parser tests here, not `unit/`).

RED-before-GREEN: written before either method existed. Proves:
  - `reassure_imports` orders by `COALESCE(created_date, imported_at) DESC`
    (D2: `created_date` primary, `imported_at` fallback), and a header-less
    import's `ordering_key` honestly reports `'imported_at'`,
  - `entry_count` matches the real row count via ONE batched query — exactly
    TWO total queries, never per-row (query-count trace hook),
  - `reassure_entries` reduces `duration`/`count` INDEPENDENTLY from TWO
    separate sample tables — exactly THREE total queries, and no executed
    SQL text ever joins `reassure_duration_sample` with
    `reassure_count_sample` (invariant I1 — never zip the two series),
  - an entry with zero rows in one sample table yields `None` on that
    series, never a zero-valued `HistoryMetric`,
  - an import with zero entries returns an empty sequence, not an error.
"""

from __future__ import annotations

from pathlib import Path

from fakes import SequentialClock
from perf.adapters.store_sqlite import SqliteStore
from perf.domain.model import ReassureEntry, ReassureHeader, ReassureParseResult


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
    content_hash: str,
    header: ReassureHeader | None = None,
) -> ReassureParseResult:
    return ReassureParseResult(
        header=header,
        entries=entries,
        content_hash=content_hash,
        skipped=(),
        partial_coverage=False,
        diagnostic=None,
    )


def _store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "perf.db", clock=SequentialClock())


def _seed_import(
    store: SqliteStore,
    *,
    content_hash: str,
    header: ReassureHeader | None = None,
    entries: tuple[ReassureEntry, ...] = (),
) -> int:
    result = _result(entries=entries, content_hash=content_hash, header=header)
    import_id = store.save_reassure_import(result, f"path/{content_hash}.perf", "current")
    assert import_id is not None
    return import_id


def test_imports_order_by_created_date_then_imported_at_fallback(tmp_path: Path):
    store = _store(tmp_path)
    try:
        id_a = _seed_import(
            store,
            content_hash="h1",
            header=ReassureHeader(created_date="2026-01-01T00:00:00+00:00"),
        )
        id_b = _seed_import(store, content_hash="h2", header=None)  # header-less
        id_c = _seed_import(
            store,
            content_hash="h3",
            header=ReassureHeader(created_date="2026-01-03T00:00:00+00:00"),
        )
        rows = store.reassure_imports(10)
    finally:
        store.close()

    # h3's created_date (2026-01-03) is the newest; h1's (2026-01-01) next;
    # h2 has no created_date, so it falls back to `imported_at` — the
    # SequentialClock's timestamps are ~2020, far older than either header.
    assert [row.import_id for row in rows] == [id_c, id_a, id_b]
    assert rows[0].ordering_key == "created_date"
    assert rows[1].ordering_key == "created_date"
    assert rows[2].ordering_key == "imported_at"
    assert rows[2].created_date is None
    assert rows[2].ordered_at == rows[2].imported_at


def test_entry_count_uses_one_batched_query_exactly_two_total(tmp_path: Path):
    store = _store(tmp_path)
    try:
        id_a = _seed_import(
            store,
            content_hash="h1",
            entries=(_entry(name="a"), _entry(name="b")),
        )
        id_b = _seed_import(store, content_hash="h2", entries=(_entry(name="c"),))

        queries: list[str] = []
        store._conn.set_trace_callback(queries.append)
        try:
            rows = store.reassure_imports(10)
        finally:
            store._conn.set_trace_callback(None)
    finally:
        store.close()

    by_id = {row.import_id: row for row in rows}
    assert by_id[id_a].entry_count == 2
    assert by_id[id_b].entry_count == 1
    assert len(queries) == 2


def test_entries_reduces_duration_and_count_independently_exactly_three_queries(
    tmp_path: Path,
):
    store = _store(tmp_path)
    try:
        entry = _entry(
            durations=(10.1, 10.2, 10.3, 10.4, 10.5, 10.6),
            counts=(1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 1.0, 1.0),
        )
        import_id = _seed_import(store, content_hash="h1", entries=(entry,))

        queries: list[str] = []
        store._conn.set_trace_callback(queries.append)
        try:
            rows = store.reassure_entries(import_id)
        finally:
            store._conn.set_trace_callback(None)
    finally:
        store.close()

    assert len(rows) == 1
    row = rows[0]
    assert row.duration is not None
    assert row.count is not None
    assert row.duration.n == 6
    assert row.count.n == 8
    assert row.duration.metric_name == "duration_ms"
    assert row.duration.unit == "ms"
    assert row.count.metric_name == "render_count"
    assert row.count.unit == "count"
    assert len(queries) == 3
    # [load-bearing] I1 — no executed SQL text ever joins the two sample
    # tables; each series is reduced by its own independent query.
    assert not any(
        "reassure_duration_sample" in query and "reassure_count_sample" in query
        for query in queries
    )


def test_entry_with_empty_duration_series_yields_none_not_zero_metric(tmp_path: Path):
    store = _store(tmp_path)
    try:
        entry = _entry(name="AllOutliersDropped", durations=(), counts=(1.0, 2.0, 3.0))
        import_id = _seed_import(store, content_hash="h1", entries=(entry,))
        rows = store.reassure_entries(import_id)
    finally:
        store.close()

    assert len(rows) == 1
    assert rows[0].duration is None
    assert rows[0].count is not None
    assert rows[0].count.n == 3


def test_import_with_zero_entries_returns_empty_sequence(tmp_path: Path):
    store = _store(tmp_path)
    try:
        import_id = _seed_import(store, content_hash="h1", entries=())
        rows = store.reassure_entries(import_id)
    finally:
        store.close()

    assert rows == ()
