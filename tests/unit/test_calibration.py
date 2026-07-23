"""Pure config sanity label (design "Calibration contract", decision #58 —
the honest-degenerate label). Tasks 1.9 RED / 1.10 GREEN. Table cases for
each label branch plus the anti-lying-label invariant: a stable/genuinely
non-flagging history must NOT be mislabelled `too-loose`.

PR-C review fix (CRITICAL): `too-loose` is defined by SUPPRESSION, not by
`floor >= max_abs`. The old rule mislabelled every dead-stable baseline
(zero/tiny deltas) `too-loose`, since a calm history trivially has
`max_abs` below any nonzero floor. The corrected rule requires a concrete
walk-forward step whose `|delta_pct| >= threshold_pct` (the percentage
threshold WOULD have flagged it) but whose `|delta_abs| < floor` (the
floor actually suppressed it) — evidence, not a coincidence of scale.
"""

from __future__ import annotations

from perf.domain import calibration, regression


def _points(commit_series: list[tuple[str, list[float]]]) -> list[tuple[str, float, str]]:
    """Builds `(git_commit, value, started_at)` rows in chronological
    commit order, mirroring the exact shape `baseline_points` returns."""

    points: list[tuple[str, float, str]] = []
    for i, (commit, values) in enumerate(commit_series):
        for j, value in enumerate(values):
            points.append((commit, value, f"2026-01-{i + 1:02d}T{j:02d}:00:00Z"))
    return points


def test_too_loose_when_floor_suppresses_a_pct_significant_step():
    """New definition (PR-C review fix): `too-loose` requires a CONCRETE
    suppressed step — a small-scale metric where a step crosses
    `threshold_pct` (6% >= 5%) but its absolute magnitude (0.06) is below
    the `floor` (5.0), so the floor actually swallowed a change the
    percentage threshold would otherwise have flagged."""
    points = _points([("c0", [1.00]), ("c1", [1.06])])
    result = calibration.grade(
        points,
        metric_name="total_time_ms",
        unit="ms",
        higher_is_better=False,
        floor=5.0,
        threshold_pct=5.0,
    )
    assert result.status == calibration.STATUS_TOO_LOOSE
    assert result.flagged_count == 0  # the floor suppressed it, so it never actually flagged


def test_stable_baseline_is_reasonable_not_too_loose():
    """CRITICAL regression case (PR-C review): a calm baseline where every
    step's `%`-change stays below `threshold_pct` must grade `reasonable`,
    NOT `too-loose` — nothing was ever suppressed, since nothing ever
    crossed the threshold in the first place. This is exactly the demo's
    dead-stable-baseline scenario the old `floor >= max_abs` rule got
    wrong."""
    points = _points([("c0", [100.0]), ("c1", [100.5]), ("c2", [99.8]), ("c3", [100.2])])
    result = calibration.grade(
        points, metric_name="m", unit="ms", higher_is_better=False, floor=5.0, threshold_pct=5.0
    )
    assert result.status == calibration.STATUS_REASONABLE


def test_too_strict_when_threshold_below_typical_run_to_run_noise():
    series = [
        ("c0", [100.0, 100.0]),
        ("c1", [100.0, 130.0]),
        ("c2", [102.0, 128.0]),
        ("c3", [101.0, 129.0]),
    ]
    points = _points(series)
    result = calibration.grade(
        points, metric_name="m", unit="ms", higher_is_better=False, floor=0.0, threshold_pct=0.5
    )
    assert result.status == calibration.STATUS_TOO_STRICT


def test_reasonable_reports_exact_flag_count():
    series = [
        ("c0", [100.0]),
        ("c1", [101.0]),
        ("c2", [99.0]),
        ("c3", [100.0]),
        ("c4", [140.0]),  # clear one-off regression jump
    ]
    points = _points(series)
    result = calibration.grade(
        points, metric_name="m", unit="ms", higher_is_better=False, floor=5.0, threshold_pct=5.0
    )
    assert result.status == calibration.STATUS_REASONABLE
    assert result.total_count == 4
    assert result.flagged_count == 1


def test_zero_flagged_is_reasonable_not_too_loose_when_floor_below_max_abs():
    """decision #58 anti-lying-label: 0-of-N flagged does NOT imply
    `too-loose` when the floor is still below the max observed `|delta|` —
    here the biggest swing is a genuine IMPROVEMENT, never a regression, so
    it legitimately never flags, yet the config could have caught a real
    regression of that magnitude."""
    points = _points([("c0", [100.0]), ("c1", [100.0]), ("c2", [100.0]), ("c3", [70.0])])
    result = calibration.grade(
        points,
        metric_name="total_time_ms",
        unit="ms",
        higher_is_better=False,
        floor=5.0,
        threshold_pct=5.0,
    )
    assert result.flagged_count == 0
    assert result.max_abs == 30.0
    assert result.status == calibration.STATUS_REASONABLE


def test_fewer_than_two_commits_is_insufficient_data_no_warn():
    points = _points([("c0", [100.0])])
    result = calibration.grade(
        points, metric_name="m", unit="ms", higher_is_better=False, floor=5.0, threshold_pct=5.0
    )
    assert result.status == calibration.STATUS_INSUFFICIENT_DATA


def test_calibration_never_alters_an_independently_computed_verdict():
    """spec 'Config Sanity Label': grading is purely informational and
    must NEVER change a `Verdict.status` (nor, by extension, the exit
    code) — grading the label must not mutate or influence a separately
    computed verdict for the same data."""
    points = _points([("c0", [100.0]), ("c1", [101.0]), ("c2", [140.0])])
    kwargs = dict(
        unit="ms",
        higher_is_better=False,
        threshold_pct=5.0,
        floor=5.0,
        baseline_commit_n=3,
        sample_n=3,
        min_n=3,
    )
    verdict_before = regression.classify("m", 140.0, 100.5, **kwargs)
    calibration.grade(
        points, metric_name="m", unit="ms", higher_is_better=False, floor=5.0, threshold_pct=5.0
    )
    verdict_after = regression.classify("m", 140.0, 100.5, **kwargs)
    assert verdict_before == verdict_after


def test_grade_all_aggregates_worst_status_and_flagged_union():
    # Same 5 commits observed by both metrics (realistic: one run yields
    # values for every metric). dur_ms is a genuinely reasonable config
    # (flags the one clear c4 jump); fps_avg's huge floor genuinely
    # SUPPRESSES a pct-significant step at c4 (~5.7% >= threshold, but
    # abs delta ~2.85 < floor 1000) — the new suppression-based definition.
    commits = [("c0", [100.0]), ("c1", [101.0]), ("c2", [99.0]), ("c3", [100.0]), ("c4", [140.0])]
    points_a = _points(commits)
    fps_commits = [("c0", [50.0]), ("c1", [50.1]), ("c2", [50.2]), ("c3", [50.3]), ("c4", [53.0])]
    points_b = _points(fps_commits)

    report = calibration.grade_all(
        {"dur_ms": points_a, "fps_avg": points_b},
        floors={"ms": 5.0, "fps": 1000.0},
        threshold_pct=5.0,
        units={"dur_ms": "ms", "fps_avg": "fps"},
        higher_is_better={"dur_ms": False, "fps_avg": True},
    )

    assert report.runs_total == 5
    flagged_commits = {commit for metric in report.metrics for commit in metric.flagged_commits}
    assert flagged_commits == {"c4"}
    assert report.status == calibration.STATUS_TOO_LOOSE  # fps_avg's suppressed step dominates


def test_grade_all_all_insufficient_data_is_insufficient_not_reasonable():
    """Review finding (PR-A): when EVERY metric grades insufficient-data
    (e.g. a brand-new flow with a single commit), the aggregate MUST surface
    insufficient-data — never a reassuring 'reasonable — 0 of 1 would flag'
    with no evidence to stand on (decision #58 anti-lying-label, aggregate
    level)."""
    report = calibration.grade_all(
        {"total_time_ms": _points([("c0", [100.0])])},
        floors={"ms": 5.0},
        threshold_pct=5.0,
        units={"total_time_ms": "ms"},
        higher_is_better={"total_time_ms": False},
    )
    assert report.metrics[0].status == calibration.STATUS_INSUFFICIENT_DATA
    assert report.status == calibration.STATUS_INSUFFICIENT_DATA


def test_grade_all_too_strict_outranks_too_loose_deterministically():
    """Review finding (PR-A): a mix of degenerate statuses must resolve
    deterministically (too-strict > too-loose), and neither per-metric status
    is lost — both remain in report.metrics for per-metric rendering."""
    # `loose`: steps of ~0.6% each (>= threshold_pct=0.5) but tiny absolute
    # magnitude (~0.6) versus the huge `ms` floor (1000.0) -> genuinely
    # suppressed under the new definition -> too-loose.
    loose = _points([(f"c{i}", [100.0 + i * 0.6]) for i in range(6)])
    strict = _points(
        [
            ("c0", [100.0, 100.0]),
            ("c1", [100.0, 130.0]),
            ("c2", [102.0, 128.0]),
            ("c3", [101.0, 129.0]),
        ]
    )
    report = calibration.grade_all(
        {"dur_ms": loose, "fps_avg": strict},
        floors={
            "ms": 1000.0
        },  # huge floor for ms -> dur_ms's small steps get suppressed (too-loose); fps floor defaults 0
        threshold_pct=0.5,  # below fps_avg's run-to-run noise -> too-strict
        units={"dur_ms": "ms", "fps_avg": "fps"},
        higher_is_better={"dur_ms": False, "fps_avg": True},
    )
    per_metric_statuses = {m.status for m in report.metrics}
    assert calibration.STATUS_TOO_LOOSE in per_metric_statuses
    assert calibration.STATUS_TOO_STRICT in per_metric_statuses
    assert report.status == calibration.STATUS_TOO_STRICT  # deterministic precedence


def test_grade_all_reasonable_when_some_evidence_even_if_another_metric_insufficient():
    """A metric with real evidence (reasonable) outranks a metric that could
    not be graded (insufficient-data) — 'reasonable' is honest here because at
    least one metric HAD enough history."""
    reasonable = _points(
        [("c0", [100.0]), ("c1", [101.0]), ("c2", [99.0]), ("c3", [100.0]), ("c4", [140.0])]
    )
    insufficient = _points([("c0", [200.0])])  # single commit -> insufficient
    report = calibration.grade_all(
        {"graded_ms": reasonable, "new_ms": insufficient},
        floors={"ms": 5.0},
        threshold_pct=5.0,
        units={"graded_ms": "ms", "new_ms": "ms"},
        higher_is_better={"graded_ms": False, "new_ms": False},
    )
    assert report.status == calibration.STATUS_REASONABLE
