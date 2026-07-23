"""Baseline read-model integration tests (design Rev 3 "Bounded baseline
query shape" + "One query, two consumers"; spec "Baseline Correctness" /
"Bounded Compare Performance"). PR-B (`compare` Phase 2, task 2.1/2.2/2.2a).

RED-before-GREEN: written before `SqliteStore.baseline_measure_points`,
`baseline_system_sample_points`, `latest_run`, `latest_measure_summary`,
`latest_system_sample_points` existed. Proves, against a seeded
MULTI-COMMIT temp SQLite history:
  - same-commit runs collapse to ONE median point (not N),
  - dev-bundle runs and the current commit are excluded,
  - warm/cold and `device_key` never mix,
  - the median-by-commit baseline DIFFERS from a naive last-N-RUNS window
    (`Store.history`, the existing per-metric `Store` Protocol read),
  - ONE query returns rows for the WHOLE metric family (batched, no
    per-metric fan-out), bounded/`LIMIT`ed to `baseline_n` commits,
  - corner cases C7 (unseen device/mode), C8 (mode split), C9
    (dev-bundle-only) all yield an empty result set (never a crash) —
    the analyzer maps these to `insufficient-data`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from fakes import SequentialClock  # noqa: E402

from perf.adapters.store_sqlite import SqliteStore  # noqa: E402
from perf.domain import statistics  # noqa: E402
from perf.domain.model import Marker, RunContext, SystemSample  # noqa: E402

FLOW = "checkout"
DEVICE_A = "Pixel 8 Pro|Android 14|physical"
DEVICE_B = "Pixel 6|Android 13|physical"


def _ctx(**overrides) -> RunContext:
    defaults = dict(
        device_key=DEVICE_A,
        model="Pixel 8 Pro",
        os_version="Android 14",
        is_emulator=False,
        source="local:eduardo",
        git_commit="c0",
        git_branch="main",
        app_version="1.0.0",
        is_dev_bundle=False,
        bundle_source="embedded",
        build_variant="release",
        tool_version="0.1.0",
    )
    defaults.update(overrides)
    return RunContext(**defaults)


def _seed_run(
    store: SqliteStore,
    *,
    git_commit,
    value_ms: float = 100.0,
    mode: str = "warm",
    device_key: str = DEVICE_A,
    is_dev_bundle: bool = False,
    metric_name: str = "checkout",
    flow: str = FLOW,
    fps_avg=None,
    ram_avg_mb=None,
) -> int:
    ctx = _ctx(git_commit=git_commit, device_key=device_key, is_dev_bundle=is_dev_bundle)
    # 3 identical measures (not 1): `run_metric_summary.p90_ms` truncates
    # `CAST(0.9*n AS INT)` — for n=1 that is CAST(0.9)=0, so NOTHING
    # qualifies and p90_ms is NULL. Repeating the SAME value 3x keeps every
    # assertion in this file unchanged (percentile of identical values ==
    # that value) while giving the view a well-defined (non-NULL) p90 —
    # this also matches the realistic shape (a marker fires once per
    # iteration, so `n` == the run's iteration count).
    markers = [Marker(name=metric_name, value=value_ms, unit="ms") for _ in range(3)]
    samples = []
    if fps_avg is not None or ram_avg_mb is not None:
        for idx in (0, 1):
            samples.append(
                SystemSample(
                    iteration_idx=idx,
                    total_time_ms=None,
                    start_time_ms=None,
                    fps_avg=fps_avg,
                    fps_min=None,
                    ram_avg_mb=ram_avg_mb,
                    ram_peak_mb=None,
                    cpu_avg_pct=None,
                    cpu_peak_pct=None,
                )
            )
    return store.save_run(ctx, flow, 1, mode, "local:eduardo", markers, samples, None)


def _store(tmp_path) -> SqliteStore:
    return SqliteStore(tmp_path / "perf.db", clock=SequentialClock())


# ===== Baseline correctness (measure family) =====


def test_baseline_measure_points_pre_collapse_rows_median_by_commit_correctly(tmp_path):
    """Commit C with 3 recorded runs of DIFFERING values contributes 3
    pre-collapse rows from the store, but `statistics.median_by_commit`
    applied on top collapses them to exactly ONE median point (spec
    'Repeated same-commit runs collapse')."""
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", value_ms=100.0)
        _seed_run(store, git_commit="c1", value_ms=200.0)
        _seed_run(store, git_commit="c1", value_ms=300.0)
        current_run_id = _seed_run(store, git_commit="HEAD", value_ms=999.0)
        latest = store.latest_run(FLOW, DEVICE_A, "warm")
        assert latest.run_id == current_run_id

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", latest.git_commit, 10)
        c1_values = [row.value for row in rows if row.git_commit == "c1"]
        assert sorted(c1_values) == [100.0, 200.0, 300.0]  # pre-collapse: all 3 present

        medians = statistics.median_by_commit((row.git_commit, row.value) for row in rows)
        assert medians["c1"] == 200.0  # collapsed to ONE median point, not 3
    finally:
        store.close()


def test_baseline_measure_points_excludes_dev_bundle_and_current_commit(tmp_path):
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", value_ms=100.0)
        _seed_run(store, git_commit="c2-dev", value_ms=500.0, is_dev_bundle=True)
        current_run_id = _seed_run(store, git_commit="HEAD", value_ms=999.0)
        latest = store.latest_run(FLOW, DEVICE_A, "warm")
        assert latest.run_id == current_run_id

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", latest.git_commit, 10)
        commits = {row.git_commit for row in rows}

        assert "c2-dev" not in commits  # dev-bundle excluded
        assert "HEAD" not in commits  # current commit excluded from its own baseline
        assert commits == {"c1"}
    finally:
        store.close()


def test_baseline_measure_points_warm_cold_and_device_never_mix(tmp_path):
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", value_ms=100.0, mode="warm", device_key=DEVICE_A)
        _seed_run(store, git_commit="c2", value_ms=200.0, mode="cold", device_key=DEVICE_A)
        _seed_run(store, git_commit="c3", value_ms=300.0, mode="warm", device_key=DEVICE_B)

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", None, 10)
        commits = {row.git_commit for row in rows}

        assert commits == {"c1"}  # cold (c2) and device B (c3) excluded
    finally:
        store.close()


def test_baseline_measure_points_naive_last_n_runs_window_gives_different_wrong_baseline(tmp_path):
    """Spec 'Naive last-10-RUNS window gives a different, wrong baseline':
    commit A has 4 runs, commit B has 1 run — a naive per-RUN window
    over-weights A. `Store.history` (the existing naive per-metric read)
    is used to build that WRONG window; `baseline_measure_points` +
    `median_by_commit` is used to build the CORRECT one; they must
    diverge."""
    store = _store(tmp_path)
    try:
        for _ in range(4):
            _seed_run(store, git_commit="commit-a", value_ms=100.0)
        _seed_run(store, git_commit="commit-b", value_ms=500.0)

        naive_rows = store.history(FLOW, "checkout", DEVICE_A, 5)
        naive_baseline = statistics.median([row.value for row in naive_rows])

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", None, 10)
        medians = statistics.median_by_commit((row.git_commit, row.value) for row in rows)
        correct_baseline = statistics.median(list(medians.values()))

        assert naive_baseline == 100.0  # over-weighted by commit-a's 4 runs
        assert correct_baseline == 300.0  # median(100, 500) — commit-a and commit-b weigh equally
        assert naive_baseline != correct_baseline
    finally:
        store.close()


def test_baseline_measure_points_batches_whole_metric_family_in_one_query(tmp_path):
    """Rev 3: no per-metric `WHERE m.name=?` filter — ONE call returns rows
    for EVERY measure-family metric (a `metric_name` column distinguishes
    them), so `compare` never issues one query per metric."""
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", value_ms=100.0, metric_name="checkout")
        _seed_run(store, git_commit="c1", value_ms=50.0, metric_name="product_view")

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", None, 10)
        metric_names = {row.metric_name for row in rows}

        assert metric_names == {"checkout", "product_view"}
    finally:
        store.close()


def test_baseline_measure_points_excludes_null_p90_n1_runs(tmp_path):
    """FIX 1 (BLOCKER, PR-B review): `run_metric_summary.p90_ms` is NULL
    when a run has exactly 1 measure for a metric (`CAST(0.9*1 AS INT)`
    truncates to 0, so nothing qualifies as p90) — reachable via
    `perf run --iterations 1`. Such a run must contribute NO baseline
    point at all (mirrors the `system_sample` path's
    `s.{field} IS NOT NULL` filter) — never a `None` value that would
    crash `statistics.median_by_commit` downstream."""
    store = _store(tmp_path)
    try:
        ctx = _ctx(git_commit="c1-n1")
        n1_marker = [Marker(name="checkout", value=999.0, unit="ms")]  # n=1 -> NULL p90_ms
        store.save_run(ctx, FLOW, 1, "warm", "local:eduardo", n1_marker, [], None)
        _seed_run(store, git_commit="c2", value_ms=100.0)

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", None, 10)
        commits = {row.git_commit for row in rows}

        assert "c1-n1" not in commits  # NULL p90 excluded entirely, not passed through as None
        assert commits == {"c2"}
    finally:
        store.close()


def test_baseline_measure_points_limited_to_baseline_n_commits(tmp_path):
    store = _store(tmp_path)
    try:
        for i in range(8):
            _seed_run(store, git_commit=f"c{i}", value_ms=float(i))

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", None, 3)
        commits = {row.git_commit for row in rows}

        assert len(commits) == 3
        assert commits == {"c5", "c6", "c7"}  # the 3 MOST RECENT commits (SequentialClock order)
    finally:
        store.close()


# ===== Corner cases (Rev 3: C7/C8/C9) =====


def test_baseline_measure_points_unseen_device_returns_empty(tmp_path):
    """C7: an unseen device/mode combination yields an EMPTY result set —
    the analyzer maps this to `insufficient-data`, never a crash."""
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", value_ms=100.0, device_key=DEVICE_A)

        rows = store.baseline_measure_points(FLOW, "Unknown Device|1|physical", "warm", None, 10)

        assert rows == []
    finally:
        store.close()


def test_baseline_measure_points_mode_split_returns_empty(tmp_path):
    """C8: history contains only cold runs but the evaluated mode is warm
    (or vice versa) — the baseline for the queried mode is empty."""
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", value_ms=100.0, mode="cold")

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", None, 10)

        assert rows == []
    finally:
        store.close()


def test_baseline_measure_points_dev_bundle_only_history_returns_empty(tmp_path):
    """C9: every prior run is a dev-bundle run — baseline is empty once
    dev bundles are excluded."""
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", value_ms=100.0, is_dev_bundle=True)
        _seed_run(store, git_commit="c2", value_ms=200.0, is_dev_bundle=True)

        rows = store.baseline_measure_points(FLOW, DEVICE_A, "warm", None, 10)

        assert rows == []
    finally:
        store.close()


# ===== Latest-run reads =====


def test_latest_run_returns_most_recent_run_for_flow_device_mode(tmp_path):
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", value_ms=100.0)
        _seed_run(store, git_commit="c2", value_ms=200.0)
        latest_run_id = _seed_run(store, git_commit="c3", value_ms=300.0)

        latest = store.latest_run(FLOW, DEVICE_A, "warm")

        assert latest is not None
        assert latest.run_id == latest_run_id
        assert latest.git_commit == "c3"
    finally:
        store.close()


def test_latest_run_returns_none_for_unknown_flow(tmp_path):
    store = _store(tmp_path)
    try:
        assert store.latest_run("no-such-flow", DEVICE_A, "warm") is None
    finally:
        store.close()


def test_latest_measure_summary_returns_metric_metadata_and_percentile(tmp_path):
    store = _store(tmp_path)
    try:
        ctx = _ctx(git_commit="c1")
        markers = [
            Marker(name="checkout", value=90.0, unit="ms"),
            Marker(name="checkout", value=100.0, unit="ms"),
            Marker(name="checkout", value=110.0, unit="ms"),
        ]
        run_id = store.save_run(ctx, FLOW, 1, "warm", "local:eduardo", markers, [], None)

        summary = store.latest_measure_summary(run_id)

        assert len(summary) == 1
        point = summary[0]
        assert point.metric_name == "checkout"
        assert point.unit == "ms"
        assert point.higher_is_better is False
        assert point.sample_n == 3
        # `run_metric_summary.p90_ms` (db/schema.sql §9.3) truncates
        # `CAST(0.9*n AS INT)`, NOT nearest-rank: for n=3, CAST(2.7)=2, so
        # rn<=2 (values 90, 100) qualify and MAX picks 100 — the SQL view's
        # own convention, distinct from `domain/statistics.percentile`'s
        # nearest-rank rule (which the analyzer uses for system_sample).
        assert point.p90_ms == 100.0
    finally:
        store.close()


def test_latest_system_sample_points_returns_raw_iteration_rows(tmp_path):
    store = _store(tmp_path)
    try:
        run_id = _seed_run(store, git_commit="c1", fps_avg=58.0)
        # _seed_run seeds 2 iterations (idx 0 and 1) with the SAME fps_avg
        # to keep this test focused on shape, not aggregation.

        raw = store.latest_system_sample_points(run_id)
        fps_rows = [row for row in raw if row.metric_name == "fps_avg"]

        assert {row.iteration_idx for row in fps_rows} == {0, 1}  # idx 0 present: no warm-up drop here
        assert all(row.value == 58.0 for row in fps_rows)
    finally:
        store.close()


# ===== System_sample family baseline read-model =====


def test_baseline_system_sample_points_batches_family_and_includes_idx_zero(tmp_path):
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", fps_avg=58.0, ram_avg_mb=200.0)
        current_run_id = _seed_run(store, git_commit="HEAD", fps_avg=10.0, ram_avg_mb=10.0)
        latest = store.latest_run(FLOW, DEVICE_A, "warm")
        assert latest.run_id == current_run_id

        rows = store.baseline_system_sample_points(FLOW, DEVICE_A, "warm", latest.git_commit, 10)
        metric_names = {row.metric_name for row in rows}
        commits = {row.git_commit for row in rows}
        idxs = {row.iteration_idx for row in rows if row.metric_name == "fps_avg"}

        assert {"fps_avg", "ram_avg_mb"} <= metric_names  # batched, no per-metric fan-out
        assert commits == {"c1"}  # current commit excluded
        assert 0 in idxs  # no warm-up filtering at the store layer — that's the analyzer's job
    finally:
        store.close()


def test_baseline_system_sample_points_dev_bundle_only_history_returns_empty(tmp_path):
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", fps_avg=58.0, is_dev_bundle=True)

        rows = store.baseline_system_sample_points(FLOW, DEVICE_A, "warm", None, 10)

        assert rows == []
    finally:
        store.close()
