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

from collections import defaultdict

from fakes import SequentialClock
from perf.adapters.store_sqlite import SqliteStore
from perf.domain import (
    calibration,
    regression,
)
from perf.domain.calibration import CalibrationReport
from perf.domain.model import CompareResult, Marker, RunContext, SystemSample

FLOW = "checkout"
DEVICE_A = "Pixel 8 Pro|Android 14|physical"

_FLOORS = {"ms": 5.0, "mb": 5.0, "pct": 3.0, "fps": 2.0}


def _ctx(**overrides) -> RunContext:
    defaults = {
        "device_key": DEVICE_A,
        "model": "Pixel 8 Pro",
        "os_version": "Android 14",
        "is_emulator": False,
        "source": "local:eduardo",
        "git_commit": "c0",
        "git_branch": "main",
        "app_version": "1.0.0",
        "is_dev_bundle": False,
        "bundle_source": "embedded",
        "build_variant": "release",
        "tool_version": "0.1.0",
    }
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
        for idx, (fps, ram) in enumerate(zip(fps_values, ram_values, strict=False))
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


def _seed_n1(store, *, git_commit, checkout_ms, fps_values=(60.0,), ram_values=(200.0,)):
    """Seeds a run with exactly ONE `checkout` marker (n=1). After the p90
    CEIL nearest-rank fix (0003_fix_p90_ceil_rank.sql) such a run has a
    WELL-DEFINED p90 — its single value (rank ceil(0.9)=1) — so it now
    CONTRIBUTES a baseline/latest point rather than yielding NULL. Still
    reachable via `perf run --iterations 1`; `compare_latest` must never
    crash on it."""
    ctx = _ctx(git_commit=git_commit)
    markers = [Marker(name="checkout", value=checkout_ms, unit="ms")]  # n=1 -> p90 == value
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

    params = {
        "threshold_pct": 5.0,
        "floors": _FLOORS,
        "min_baseline_commits": 2,
        "warmup_k": 1,
        "baseline_n": 10,
    }
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
        # Baseline commits c1/c2/c3 give EVERY metric identical values (zero
        # variance) — with the corrected suppression-based `too-loose`
        # definition (PR-C review fix), a baseline that never crosses
        # `threshold_pct` grades `reasonable`, not `too-loose`.
        assert result.calibration.status == calibration.STATUS_REASONABLE

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


def test_verdict_carries_higher_is_better_for_both_families(tmp_path):
    """Audit fix: `Verdict.higher_is_better` must be populated with the
    SAME direction `classify` actually used for each family — the measure
    family threads `RunPoint.higher_is_better` (persisted at ingestion,
    `store_sqlite.py`), the `system_sample` family threads
    `default_higher_is_better(metric_name)` computed in `analyzer_sql.py`
    itself. `contracts/compare_v1.py` reads this field directly rather
    than re-deriving it by metric name at serialization time."""
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
        _seed(store, git_commit="HEAD", checkout_ms=100.0, fps_values=[60.0], ram_values=[200.0])

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        checkout = _verdict_by_metric(result, "checkout")  # measure family, lower-is-better
        fps = _verdict_by_metric(result, "fps_avg")  # system_sample family, higher-is-better
        ram = _verdict_by_metric(result, "ram_avg_mb")  # system_sample family, lower-is-better

        assert checkout is not None and checkout.higher_is_better is False
        assert fps is not None and fps.higher_is_better is True
        assert ram is not None and ram.higher_is_better is False
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


# ===== Adaptive noise floor (anti-false-positive batch, Task 2): a noisy
# baseline widens the effective floor so a small delta that a STATIC floor
# would flag is suppressed to `stable`; `adaptive_floor=False` restores it. =====


def _seed_noisy_baseline_and_small_delta_latest(store):
    """5 baseline commits with checkout medians 100,90,110,95,105 (robust
    noise 1.4826*MAD == 1.4826*5 ≈ 7.41 -> 2* ≈ 14.83), then a LATEST run
    +8ms over the baseline median (100). +8 clears the static ms floor (5.0)
    and the 5% threshold, so it flags under a static floor — but 8 < 14.83,
    so the adaptive floor suppresses it."""
    for commit, value in (("c1", 100.0), ("c2", 90.0), ("c3", 110.0), ("c4", 95.0), ("c5", 105.0)):
        _seed(
            store,
            git_commit=commit,
            checkout_ms=value,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )
    _seed(
        store,
        git_commit="HEAD",
        checkout_ms=108.0,
        fps_values=[60.0, 60.0],
        ram_values=[200.0, 200.0],
    )


def test_adaptive_floor_suppresses_small_delta_on_noisy_baseline(tmp_path):
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        _seed_noisy_baseline_and_small_delta_latest(store)

        analyzer = _make_analyzer(store)  # adaptive_floor defaults True
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        checkout = _verdict_by_metric(result, "checkout")
        assert checkout is not None
        assert checkout.baseline_value == 100.0
        assert checkout.status == regression.STATUS_STABLE  # noise floor swallows the +8
        # `Verdict.floor` reports the ACTUAL (widened) floor used, not the
        # static 5.0 — 2 * robust_noise([100,90,110,95,105]) == 2*1.4826*5.
        assert checkout.floor == 2.0 * 1.4826 * 5.0
    finally:
        store.close()


def test_adaptive_floor_false_restores_static_floor_verdict(tmp_path):
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        _seed_noisy_baseline_and_small_delta_latest(store)

        analyzer = _make_analyzer(store, adaptive_floor=False)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        checkout = _verdict_by_metric(result, "checkout")
        assert checkout is not None
        # +8 clears the static ms floor (5.0) and the 5% threshold -> regression,
        # and `Verdict.floor` is the static floor, unwidened.
        assert checkout.status == regression.STATUS_REGRESSION
        assert checkout.floor == 5.0
    finally:
        store.close()


def test_new_metric_in_latest_absent_from_baseline_is_insufficient_data(tmp_path):
    """C5: a metric present in the LATEST run but absent from every
    baseline commit classifies `insufficient-data`, never crashes."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        for commit in ("c1", "c2"):
            _seed(
                store, git_commit=commit, checkout_ms=100.0, fps_values=[60.0], ram_values=[200.0]
            )
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


def test_verdict_series_is_chronological_baseline_medians_plus_latest(tmp_path):
    """PR-C (CLI sparkline, task 3.4) needs `Verdict.series` populated —
    chronological per-commit baseline medians (oldest first), with the
    LATEST run's value appended last, so `compare_pretty.render_compare`
    can draw a trend sparkline ending at "now". Reuses the SAME per-run
    rows the baseline query already returned (no second query — design
    'One query, two consumers')."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        for commit, value in (("c1", 100.0), ("c2", 110.0), ("c3", 105.0)):
            _seed(
                store, git_commit=commit, checkout_ms=value, fps_values=[60.0], ram_values=[200.0]
            )
        _seed(store, git_commit="HEAD", checkout_ms=120.0, fps_values=[60.0], ram_values=[200.0])

        analyzer = _make_analyzer(store, min_baseline_commits=2)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        checkout = _verdict_by_metric(result, "checkout")
        assert checkout is not None
        # c1, c2, c3 baseline medians in chronological (seed) order, then
        # the latest run's own value appended last.
        assert checkout.series == (100.0, 110.0, 105.0, 120.0)
    finally:
        store.close()


def test_verdict_series_points_parity_across_both_families(tmp_path):
    """budget-check design risk #1 (highest blast radius): `series_points`
    MUST be in the SAME order as `series` for BOTH the `measure` and
    `system_sample` families — factored from the SAME sorted input
    `_sparkline_series` consumes, so drift is structurally impossible, not
    merely tested-against. Pins: length parity, per-index value parity,
    chronological order, and the LAST point's `.commit == latest.git_commit`."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        # Two system_sample iterations per run: `warmup_k=1` (the default in
        # `_make_analyzer`) drops idx0, so a single-iteration seed would
        # leave `fps_avg`/`ram_avg_mb` with zero post-warmup samples
        # (insufficient-data, empty series) — matches the pattern every
        # other system_sample seed in this file already uses.
        for commit, checkout_ms, fps, ram in (
            ("c1", 100.0, 60.0, 200.0),
            ("c2", 110.0, 61.0, 201.0),
            ("c3", 105.0, 62.0, 202.0),
        ):
            _seed(
                store,
                git_commit=commit,
                checkout_ms=checkout_ms,
                fps_values=[fps, fps],
                ram_values=[ram, ram],
            )
        _seed(
            store,
            git_commit="HEAD",
            checkout_ms=120.0,
            fps_values=[63.0, 63.0],
            ram_values=[203.0, 203.0],
        )

        analyzer = _make_analyzer(store, min_baseline_commits=2)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")
        assert result is not None

        for metric_name in ("checkout", "fps_avg", "ram_avg_mb"):
            verdict = _verdict_by_metric(result, metric_name)
            assert verdict is not None, metric_name

            assert len(verdict.series_points) == len(verdict.series), metric_name
            for i, value in enumerate(verdict.series):
                assert verdict.series_points[i].value == value, (metric_name, i)

            commits = [p.commit for p in verdict.series_points]
            assert commits == ["c1", "c2", "c3", "HEAD"], metric_name  # chronological
            assert verdict.series_points[-1].commit == "HEAD", metric_name  # == latest.git_commit
    finally:
        store.close()


# ===== Excluded-runs diagnostic counts (anti-false-positive batch, Task 4):
# runs the baseline query silently drops (current-commit, no-commit) are
# counted onto CompareResult for the pretty view — never into --json. =====


def test_compare_result_reports_excluded_run_counts(tmp_path):
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
        # a run with NO git commit (detached/no repo) -> excluded as no-commit
        _seed(
            store,
            git_commit=None,
            checkout_ms=100.0,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )
        # two runs on the CURRENT commit (HEAD); the latter is the latest run
        _seed(
            store,
            git_commit="HEAD",
            checkout_ms=100.0,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )
        _seed(
            store,
            git_commit="HEAD",
            checkout_ms=100.0,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        assert result is not None
        assert result.excluded_same_commit == 2  # both HEAD runs
        assert result.excluded_no_commit == 1  # the git_commit-less run
    finally:
        store.close()


def test_compare_result_excluded_counts_zero_with_clean_history(tmp_path):
    """No same-commit duplicates and no commit-less runs -> both counts 0, so
    the pretty view adds no note line (the common, healthy case)."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
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
            checkout_ms=100.0,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        assert result is not None
        assert result.excluded_same_commit == 1  # only the latest HEAD run itself
        assert result.excluded_no_commit == 0
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


# ===== p90 CEIL nearest-rank fix (0003_fix_p90_ceil_rank.sql): an n=1 run
# (reachable via `perf run --iterations 1`) now has a WELL-DEFINED p90 — its
# single value — so it CONTRIBUTES rather than yielding NULL, and
# `compare_latest` must never crash on it. =====


def test_n1_run_in_baseline_window_contributes_its_single_value_not_crash(tmp_path):
    """(a) A baseline window CONTAINING an n=1 run: after the ceil fix that
    run's single value IS its p90, so it CONTRIBUTES a baseline point (rank
    ceil(0.9)=1) instead of being dropped as a NULL — and never crashes
    `median_by_commit`."""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        _seed(
            store,
            git_commit="c1",
            checkout_ms=100.0,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )
        _seed_n1(store, git_commit="c2", checkout_ms=999.0)  # n=1 -> p90 == 999.0, contributes
        _seed(
            store,
            git_commit="c3",
            checkout_ms=100.0,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )
        _seed(
            store,
            git_commit="HEAD",
            checkout_ms=100.0,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )

        analyzer = _make_analyzer(store, min_baseline_commits=2)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")  # must NOT crash

        assert result is not None
        checkout = _verdict_by_metric(result, "checkout")
        assert checkout is not None
        # All three commits contribute: per-commit medians 100 (c1), 999 (c2),
        # 100 (c3) -> median-of-medians == 100.
        assert checkout.baseline_value == 100.0
        assert checkout.baseline_commit_n == 3  # c1, c2 (n=1) AND c3 all count
        assert checkout.status == regression.STATUS_STABLE
    finally:
        store.close()


def test_latest_n1_run_now_has_a_valid_p90_not_insufficient_data(tmp_path):
    """(b) The LATEST run is n=1 for a metric: after the ceil fix its single
    value IS the p90 (rank ceil(0.9)=1), so the metric gets a REAL verdict
    (here a regression against a stable baseline) instead of the old
    `insufficient-data` from a NULL latest. `compare_latest` never crashes.
    `min_baseline_commits=1` isolates this from the baseline-depth guard."""
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
        _seed_n1(store, git_commit="HEAD", checkout_ms=999.0)  # n=1 latest -> p90 == 999.0

        analyzer = _make_analyzer(store, min_baseline_commits=1)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")  # must NOT crash

        assert result is not None
        checkout = _verdict_by_metric(result, "checkout")
        assert checkout is not None
        assert checkout.latest_value == 999.0  # the single n=1 value, not None
        assert checkout.status == regression.STATUS_REGRESSION  # 999 vs 100 baseline (up = worse)
    finally:
        store.close()


def test_all_baseline_runs_n1_now_contribute_not_insufficient_data(tmp_path):
    """(c) EVERY baseline run is n=1: after the ceil fix each contributes its
    single value, so the baseline is NON-empty (two commits) and the metric
    gets a real verdict — never a crash. (Under the old floor form every
    n=1 run yielded NULL and the baseline collapsed to `insufficient-data`.)"""
    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        _seed_n1(store, git_commit="c1", checkout_ms=100.0)
        _seed_n1(store, git_commit="c2", checkout_ms=200.0)
        _seed(
            store,
            git_commit="HEAD",
            checkout_ms=100.0,
            fps_values=[60.0, 60.0],
            ram_values=[200.0, 200.0],
        )

        analyzer = _make_analyzer(store, min_baseline_commits=1)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")  # must NOT crash

        assert result is not None
        checkout = _verdict_by_metric(result, "checkout")
        assert checkout is not None
        assert checkout.baseline_commit_n == 2  # both n=1 baseline runs contribute now
        # baseline median-of-medians == median(100, 200) == 150; latest 100 is
        # a drop -> improvement (checkout is lower-is-better).
        assert checkout.status == regression.STATUS_IMPROVEMENT
    finally:
        store.close()


# ===== FIX 2 (WARNING, PR-B review): warm-up full-drop must still emit
# `insufficient-data`, not silently vanish from `result.verdicts`. =====


def test_full_warmup_drop_still_emits_insufficient_data_not_dropped_metric(tmp_path):
    """WARNING fix (PR-B review): a `system_sample` metric that loses ALL
    its samples to the warm-up drop (a single-iteration `idx=0` LATEST
    run under `warmup_k=1`) must still be PRESENT in `result.verdicts`
    with status `insufficient-data` — never silently vanish (which would
    look identical to the metric never having existed, indistinguishable
    from C6)."""
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
        # LATEST run: single iteration (idx=0 only) -> fully dropped by warmup_k=1
        _seed(store, git_commit="HEAD", checkout_ms=100.0, fps_values=[60.0], ram_values=[200.0])

        analyzer = _make_analyzer(store)
        result = analyzer.compare_latest(FLOW, DEVICE_A, "warm")

        assert result is not None
        fps = _verdict_by_metric(result, "fps_avg")
        ram = _verdict_by_metric(result, "ram_avg_mb")

        assert fps is not None  # present, not silently dropped
        assert fps.status == regression.STATUS_INSUFFICIENT_DATA
        assert fps.sample_n == 0
        assert fps.latest_value is None
        assert ram is not None  # present, not silently dropped
        assert ram.status == regression.STATUS_INSUFFICIENT_DATA
        assert ram.sample_n == 0
        assert ram.latest_value is None
    finally:
        store.close()
