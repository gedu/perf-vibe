"""Integration tests for the PR1a read-path `Store` methods —
`reassure_imports`/`reassure_entries` (design "Read Models", "Ports",
"Query budget" — this repo files store/parser tests here, not `unit/`),
plus PR1c's `reassure_import_exists`, plus PR2a's `reassure_series`.

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

`reassure_import_exists` (PR1c, added while implementing `reassure entries
<import-id>`'s usage-error check — task 1c.4's "validate `import_id`
exists" cannot be satisfied by `reassure_imports`'s WINDOWED roster (an id
outside `--limit`'s window is not "unknown") nor by `reassure_entries`'s
emptiness (a real import with zero entries and an unknown import id are
BOTH `()`, and the spec requires different outcomes for each — spec
"reassure entries <import-id>": exit `2` on unknown, exit `0` with an
empty list on a real-but-empty import). This is a narrow, UNBOUNDED
single-row lookup — the third store method design A2 did not anticipate,
flagged here rather than forced into the wrong existing method):
  - a seeded import id reports `True`,
  - an id nothing ever wrote reports `False`,
  - a real import with zero entries STILL reports `True` (existence, not
    entry count).

`reassure_series` (PR2a, design "Read Models" `ReassureSeriesPoint` /
"Ports" / "Query budget" row "`reassure history`"). RED-before-GREEN.
Proves:
  - points come back OLDEST->NEWEST, one per import containing `name`,
    exactly THREE total queries, no executed SQL text joins the two sample
    tables (invariant I1),
  - [unmissable] `limit` selects the MOST RECENT `limit` imports, not the
    oldest `limit` — a naive `ORDER BY ... ASC LIMIT ?` would silently
    return the wrong end of the series once real data exceeds `limit`,
  - [unmissable] two imports sharing `commit_hash` AND `branch` (0006's
    real baseline/current pair) stay TWO DISTINCT points, each carrying
    its OWN value — never collapsed/averaged (`statistics.median_by_commit`
    must never touch this path; `commit_hash`/`branch` are label-only),
  - an import that does NOT contain `name` contributes NOTHING and does
    not shift a neighbor's data into its slot — this is what makes the
    LAST TWO points of `reassure_series(name, limit=2)` mean "latest" and
    "immediately preceding import that also contains `name`", which
    PR2b's D5 state transition relies on,
  - `initial_update_count`'s `None` (never measured) vs `0` (measured,
    clean) survives the round trip on the nested `entry` — never collapsed.
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


def test_reassure_import_exists_true_for_a_seeded_import(tmp_path: Path):
    store = _store(tmp_path)
    try:
        import_id = _seed_import(store, content_hash="h1", entries=(_entry(),))
        assert store.reassure_import_exists(import_id) is True
    finally:
        store.close()


def test_reassure_import_exists_false_for_an_id_nothing_ever_wrote(tmp_path: Path):
    store = _store(tmp_path)
    try:
        assert store.reassure_import_exists(999999) is False
    finally:
        store.close()


def test_reassure_import_exists_true_for_a_real_import_with_zero_entries(tmp_path: Path):
    """The trap this method exists to avoid: `reassure_entries` returning
    `()` is ambiguous between "unknown import" and "real import, zero
    entries" — `reassure_import_exists` disambiguates by checking the
    `reassure_import` row directly, never entry count."""
    store = _store(tmp_path)
    try:
        import_id = _seed_import(store, content_hash="h1", entries=())
        assert store.reassure_entries(import_id) == ()
        assert store.reassure_import_exists(import_id) is True
    finally:
        store.close()


# ===== PR2a: `reassure_series` =====

_SERIES_NAME = "WidgetPanel Performance Tests WidgetPanel renders correctly"


def test_series_orders_oldest_to_newest_one_per_import_exactly_three_queries(
    tmp_path: Path,
):
    store = _store(tmp_path)
    try:
        id_a = _seed_import(
            store,
            content_hash="h1",
            header=ReassureHeader(created_date="2026-01-01T00:00:00+00:00"),
            entries=(_entry(name=_SERIES_NAME, durations=(10.0,), counts=(1.0,)),),
        )
        id_b = _seed_import(
            store,
            content_hash="h2",
            header=ReassureHeader(created_date="2026-01-02T00:00:00+00:00"),
            entries=(_entry(name=_SERIES_NAME, durations=(20.0,), counts=(2.0,)),),
        )
        id_c = _seed_import(
            store,
            content_hash="h3",
            header=ReassureHeader(created_date="2026-01-03T00:00:00+00:00"),
            entries=(_entry(name=_SERIES_NAME, durations=(30.0,), counts=(3.0,)),),
        )

        queries: list[str] = []
        store._conn.set_trace_callback(queries.append)
        try:
            points = store.reassure_series(_SERIES_NAME, 10)
        finally:
            store._conn.set_trace_callback(None)
    finally:
        store.close()

    # oldest -> newest, matching `history_runs`'s chart order.
    assert [p.import_id for p in points] == [id_a, id_b, id_c]
    assert [p.entry.duration.p50 for p in points] == [10.0, 20.0, 30.0]
    assert [p.entry.count.p50 for p in points] == [1.0, 2.0, 3.0]
    assert len(queries) == 3
    # [load-bearing] I1 — no executed SQL text ever joins the two sample
    # tables; each series is reduced by its own independent query.
    assert not any(
        "reassure_duration_sample" in query and "reassure_count_sample" in query
        for query in queries
    )


def test_series_limit_selects_most_recent_imports_not_oldest(tmp_path: Path):
    """[unmissable] A naive `ORDER BY ... ASC LIMIT ?` would return the
    OLDEST `limit` imports instead. With real data exceeding `limit`,
    `reassure_series` must select the MOST RECENT `limit` imports, then
    present them oldest-first — distinct values so a wrong window fails
    loudly rather than looking identical on a small fixture."""
    store = _store(tmp_path)
    try:
        days = ["01", "02", "03", "04", "05"]
        ids = [
            _seed_import(
                store,
                content_hash=f"h{day}",
                header=ReassureHeader(created_date=f"2026-01-{day}T00:00:00+00:00"),
                entries=(
                    _entry(
                        name=_SERIES_NAME,
                        durations=(float(10 * (index + 1)),),
                        counts=(float(index + 1),),
                    ),
                ),
            )
            for index, day in enumerate(days)
        ]

        points = store.reassure_series(_SERIES_NAME, 2)
    finally:
        store.close()

    # Most recent two imports are day 04 and day 05 (ids[3], ids[4]),
    # presented oldest-first. The oldest three (ids[0:3], values 10/20/30)
    # must be entirely absent — NOT the first two.
    assert [p.import_id for p in points] == [ids[3], ids[4]]
    assert [p.entry.duration.p50 for p in points] == [40.0, 50.0]


def test_series_same_commit_and_branch_imports_stay_two_distinct_points(tmp_path: Path):
    """[unmissable] Two imports sharing `commit_hash` AND `branch` — 0006's
    real verified case, baseline's timestamp three hours newer than
    current's — both containing `name`, must stay TWO DISTINCT points, each
    carrying its OWN value. A test that would still pass if the two
    collapsed into one averaged point is worthless — this is exactly the
    failure mode `statistics.median_by_commit` would introduce, and
    `reassure_series` must never call it nor group by `commit_hash`."""
    store = _store(tmp_path)
    try:
        shared_commit = "abc123"
        shared_branch = "main"
        id_current = _seed_import(
            store,
            content_hash="h-current",
            header=ReassureHeader(
                branch=shared_branch,
                commit_hash=shared_commit,
                created_date="2026-01-01T00:00:00+00:00",
            ),
            entries=(_entry(name=_SERIES_NAME, durations=(10.0,), counts=(1.0,)),),
        )
        id_baseline = _seed_import(
            store,
            content_hash="h-baseline",
            header=ReassureHeader(
                branch=shared_branch,
                commit_hash=shared_commit,
                created_date="2026-01-01T03:00:00+00:00",
            ),
            entries=(_entry(name=_SERIES_NAME, durations=(99.0,), counts=(9.0,)),),
        )

        points = store.reassure_series(_SERIES_NAME, 10)
    finally:
        store.close()

    assert len(points) == 2
    assert [p.import_id for p in points] == [id_current, id_baseline]
    assert points[0].commit_hash == shared_commit
    assert points[1].commit_hash == shared_commit
    assert points[0].branch == shared_branch
    assert points[1].branch == shared_branch
    assert points[0].entry.duration.p50 == 10.0
    assert points[1].entry.duration.p50 == 99.0


def test_series_import_missing_name_contributes_nothing_no_shift(tmp_path: Path):
    """`name` present in imports A and C but absent from B yields exactly
    TWO points; B contributes nothing, and neither A's nor C's data shifts
    into B's slot. This is what makes `reassure_series(name, limit=2)`'s
    last two points mean "latest" and "immediately preceding import that
    also contains `name`" — the exact semantics PR2b's D5 state transition
    relies on."""
    store = _store(tmp_path)
    try:
        other_name = "SomeOtherComponent renders correctly"
        id_a = _seed_import(
            store,
            content_hash="hA",
            header=ReassureHeader(created_date="2026-01-01T00:00:00+00:00"),
            entries=(_entry(name=_SERIES_NAME, durations=(10.0,), counts=(1.0,)),),
        )
        _seed_import(
            store,
            content_hash="hB",
            header=ReassureHeader(created_date="2026-01-02T00:00:00+00:00"),
            entries=(_entry(name=other_name, durations=(999.0,), counts=(9.0,)),),
        )
        id_c = _seed_import(
            store,
            content_hash="hC",
            header=ReassureHeader(created_date="2026-01-03T00:00:00+00:00"),
            entries=(_entry(name=_SERIES_NAME, durations=(30.0,), counts=(3.0,)),),
        )

        points = store.reassure_series(_SERIES_NAME, 10)
    finally:
        store.close()

    assert [p.import_id for p in points] == [id_a, id_c]
    assert [p.entry.duration.p50 for p in points] == [10.0, 30.0]


def test_series_preserves_initial_update_count_none_vs_zero_on_nested_entry(
    tmp_path: Path,
):
    """`initial_update_count` rides on the nested `entry` (design "Read
    Models"). `None` (never measured) and `0` (measured, clean) are
    different facts and must never collapse — the fact PR2b's D5 depends
    on."""
    store = _store(tmp_path)
    try:
        id_never_measured = _seed_import(
            store,
            content_hash="h1",
            header=ReassureHeader(created_date="2026-01-01T00:00:00+00:00"),
            entries=(_entry(name=_SERIES_NAME, initial_update_count=None),),
        )
        id_clean = _seed_import(
            store,
            content_hash="h2",
            header=ReassureHeader(created_date="2026-01-02T00:00:00+00:00"),
            entries=(_entry(name=_SERIES_NAME, initial_update_count=0),),
        )

        points = store.reassure_series(_SERIES_NAME, 10)
    finally:
        store.close()

    assert [p.import_id for p in points] == [id_never_measured, id_clean]
    assert points[0].entry.initial_update_count is None
    assert points[1].entry.initial_update_count == 0
