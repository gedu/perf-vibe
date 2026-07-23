"""`SqlAnalyzer` integration tests (design "Data Flow" / "One query, two
consumers"; spec "Direction-Aware Classification" / "Warm-Up Discard
Asymmetry"). PR-B (`compare` Phase 2, task 2.5/2.5a).

RED-before-GREEN: written before `src/perf/adapters/analyzer_sql.py`
existed. Drives `SqlAnalyzer.compare_latest` against a REAL `SqliteStore`
(temp SQLite) seeded via `save_run` — no monkeypatching of the analyzer or
the store under test. Proves:
  - direction-aware verdicts for BOTH the measure family (markers) and the
    `system_sample` family (Flashlight aggregates),
  - warm-up discard `K` drops `idx < K` for `system_sample` metrics ONLY —
    marker/measure metrics are never ordinal-filtered,
  - `calibration.grade_all` is fed the SAME per-run rows the baseline
    query already returned (single query per family, not a second one),
  - corner cases C5 (new metric, no baseline) / C6 (dropped metric,
    skipped) / C9 (dev-bundle-only baseline) never crash and never
    silently default to `stable`.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from fakes import SequentialClock  # noqa: E402

from perf.adapters.store_sqlite import SqliteStore  # noqa: E402
from perf.domain import regression  # noqa: E402
from perf.domain.calibration import CalibrationReport  # noqa: E402
from perf.domain.model import CompareResult, Marker, RunContext, SystemSample  # noqa: E402

FLOW = "checkout"
DEVICE_A = "Pixel 8 Pro|Android 14|physical"

_FLOORS = {"ms": 5.0, "mb": 5.0, "pct": 3.0, "fps": 2.0}


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


def _system_samples(fps_values, ram_values):
    return [
        SystemSample(
            iteration_idx=idx,
            total_time_ms=None,
            start_time_ms=None,
            fps_avg=fps,
            fps_min=None,
            ram_avg_mb=ram,
            ram_peak_mb=None,
            cpu_avg_pct=None,
            cpu_peak_pct=None,
        )
        for idx, (fps, ram) in enumerate(zip(fps_values, ram_values))
    ]


def _seed(
    store,
    *,
    git_commit,
    checkout_ms,
    fps_values,
    ram_values,
    is_dev_bundle=False,
    extra_markers=(),
):
    ctx = _ctx(git_commit=git_commit, is_dev_bundle=is_dev_bundle)
    markers = [Marker(name="checkout", value=checkout_ms, unit="ms") for _ in range(3)]
    markers.extend(extra_markers)
    samples = _system_samples(fps_values, ram_values)
    return store.save_run(ctx, FLOW, 1, "warm", "local:eduardo", markers, samples, None)


class _CallCountingStore(SqliteStore):
    """Spy-via-subclass (not `mock.patch`): delegates to the REAL
    `SqliteStore` implementation, only adding a call-count tally so the
    test can prove `SqlAnalyzer` issues ONE baseline query per family
    (design 'One query, two consumers' — no divergent second query for
    calibration)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.call_counts: dict = defaultdict(int)

    def baseline_measure_points(self, *args, **kwargs):
        self.call_counts["baseline_measure_points"] += 1
        return super().baseline_measure_points(*args, **kwargs)

    def baseline_system_sample_points(self, *args, **kwargs):
        self.call_counts["baseline_system_sample_points"] += 1
        return super().baseline_system_sample_points(*args, **kwargs)


def _make_analyzer(store, **overrides):
    from perf.adapters.analyzer_sql import SqlAnalyzer

    params = dict(
        threshold_pct=5.0,
        floors=_FLOORS,
        min_baseline_commits=2,
        warmup_k=1,
        baseline_n=10,
    )
    params.update(overrides)
    return SqlAnalyzer(store, **params)


def _verdict_by_metric(result: CompareResult, metric_name: str):
    for verdict in result.verdicts:
        if verdict.metric_name == metric_name:
            return verdict
    return None


def test_compare_latest_direction_aware_verdicts_across_both_families(tmp_path):
    store = _CallCountingStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        for commit in ("c1", "c2", "c3"):
            _seed(
                store,
                git_commit=commit,
                checkout_ms=100.0,
                fps_values=[60.0, 60.0],
                ram_values=[200.0, 200.0],
            )
        _seed(
            store,
            git_commit="HEAD",
            checkout_ms=200.0,  # duration UP -> regression (lower-is-better)
            fps_values=[1000.0, 30.0, 30.0],  # idx0 outlier; post-warmup [30,30] -> regression
            ram_values=[9999.0, 202.0, 202.0],  # idx0 outlier; post-warmup [202,202] -> stable
        )

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        assert result is not None
        assert isinstance(result, CompareResult)
        assert isinstance(result.calibration, CalibrationReport)

        checkout = _verdict_by_metric(result, "checkout")
        fps = _verdict_by_metric(result, "fps_avg")
        ram = _verdict_by_metric(result, "ram_avg_mb")

        assert checkout is not None and checkout.status == regression.STATUS_REGRESSION
        assert fps is not None and fps.status == regression.STATUS_REGRESSION
        assert ram is not None and ram.status == regression.STATUS_STABLE

        # design 'One query, two consumers': baseline read for each family
        # issued EXACTLY once — the SAME rows feed both the verdict AND
        # `calibration.grade_all`, never a second, divergent query.
        assert store.call_counts["baseline_measure_points"] == 1
        assert store.call_counts["baseline_system_sample_points"] == 1
    finally:
        store.close()


def test_warmup_k_drops_first_iteration_for_system_sample_only_not_measure(tmp_path):
    """spec 'Warm-Up Discard Asymmetry': `idx < K` is dropped for
    `system_sample` metrics ONLY. Marker/measure metrics ('checkout') have
    no ordinal — ALL 3 seeded measures count toward `sample_n`, while
    `fps_avg` (3 iterations seeded) loses its first (idx=0) to warm-up."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        for commit in ("c1", "c2"):
            _seed(
                store,
                git_commit=commit,
                checkout_ms=100.0,
                fps_values=[60.0, 60.0],
                ram_values=[200.0, 200.0],
            )
        _seed(
            store,
            git_commit="HEAD",
            checkout_ms=100.0,
            fps_values=[1000.0, 60.0, 60.0],
            ram_values=[200.0, 200.0, 200.0],
        )

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        checkout = _verdict_by_metric(result, "checkout")
        fps = _verdict_by_metric(result, "fps_avg")

        assert checkout.sample_n == 3  # every measure counts — no ordinal to drop
        assert fps.sample_n == 2  # 3 iterations minus the warmed-up-dropped idx 0
        assert fps.status == regression.STATUS_STABLE  # post-drop values match baseline (60.0)
    finally:
        store.close()


def test_new_metric_in_latest_absent_from_baseline_is_insufficient_data(tmp_path):
    """C5: a metric present in the LATEST run but absent from every
    baseline commit classifies `insufficient-data`, never crashes."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        for commit in ("c1", "c2"):
            _seed(store, git_commit=commit, checkout_ms=100.0, fps_values=[60.0], ram_values=[200.0])
        _seed(
            store,
            git_commit="HEAD",
            checkout_ms=100.0,
            fps_values=[60.0],
            ram_values=[200.0],
            extra_markers=[Marker(name="brand_new_metric", value=42.0, unit="ms")] * 3,
        )

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        new_metric_verdict = _verdict_by_metric(result, "brand_new_metric")
        assert new_metric_verdict is not None
        assert new_metric_verdict.status == regression.STATUS_INSUFFICIENT_DATA
    finally:
        store.close()


def test_metric_dropped_from_latest_is_skipped_not_fatal(tmp_path):
    """C6: a metric present in the baseline but ABSENT from the latest run
    is silently skipped (no `Verdict` emitted for it) — no crash."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        for commit in ("c1", "c2"):
            _seed(
                store,
                git_commit=commit,
                checkout_ms=100.0,
                fps_values=[60.0],
                ram_values=[200.0],
                extra_markers=[Marker(name="old_metric_removed_later", value=10.0, unit="ms")] * 3,
            )
        # latest run never emits "old_metric_removed_later"
        _seed(store, git_commit="HEAD", checkout_ms=100.0, fps_values=[60.0], ram_values=[200.0])

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        assert result is not None  # no crash
        assert _verdict_by_metric(result, "old_metric_removed_later") is None  # skipped
        assert _verdict_by_metric(result, "checkout") is not None  # unaffected metric still present
    finally:
        store.close()


def test_dev_bundle_only_baseline_history_is_insufficient_data_not_stable(tmp_path):
    """C9: every prior run is a dev-bundle run — the baseline is empty
    once dev bundles are excluded, so EVERY metric is `insufficient-data`,
    never a false `stable`."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        _seed(
            store,
            git_commit="c1-dev",
            checkout_ms=100.0,
            fps_values=[60.0],
            ram_values=[200.0],
            is_dev_bundle=True,
        )
        _seed(store, git_commit="HEAD", checkout_ms=100.0, fps_values=[60.0], ram_values=[200.0])

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        checkout = _verdict_by_metric(result, "checkout")
        fps = _verdict_by_metric(result, "fps_avg")

        assert checkout.status == regression.STATUS_INSUFFICIENT_DATA
        assert fps.status == regression.STATUS_INSUFFICIENT_DATA
    finally:
        store.close()


def test_compare_latest_returns_none_when_no_runs_at_all(tmp_path):
    """No prior run at all for this flow/device/mode — `SqlAnalyzer`
    returns `None` (the CLI, PR-C, maps this to the usage-error exit)."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest("no-such-flow", DEVICE_A, "warm")
        assert result is None
    finally:
        store.close()
