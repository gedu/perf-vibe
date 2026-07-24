"""Direction-aware `regression.classify` (design "Direction-Aware
Classification" + decision #39 — the FPS-drop inversion bug). Highest
blast-radius surface per tasks 1.5 RED / 1.6 GREEN / 1.11 RED (corner
cases C1/C3/C4/C5) — hypothesis-hardened.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from perf.domain.model import SeriesPoint
from perf.domain.regression import classify

_FLOOR = 5.0
_THRESHOLD_PCT = 5.0
_MIN_N = 3


def _classify(
    latest,
    baseline,
    *,
    higher_is_better,
    baseline_commit_n=_MIN_N,
    sample_n=_MIN_N,
    min_n=_MIN_N,
    floor=_FLOOR,
    threshold_pct=_THRESHOLD_PCT,
):
    return classify(
        "metric",
        latest,
        baseline,
        unit="ms",
        higher_is_better=higher_is_better,
        threshold_pct=threshold_pct,
        floor=floor,
        baseline_commit_n=baseline_commit_n,
        sample_n=sample_n,
        min_n=min_n,
    )


# ===== direction invariants (decision #39) =====


def test_fps_drop_is_a_regression():
    """A naive 'bigger number = worse' rule would wrongly call this an
    improvement (spec scenario 'FPS drop is a regression')."""
    verdict = _classify(50.0, 60.0, higher_is_better=True)
    assert verdict.status == "regression"


def test_fps_rise_is_an_improvement():
    verdict = _classify(70.0, 60.0, higher_is_better=True)
    assert verdict.status == "improvement"


def test_duration_rise_is_a_regression():
    verdict = _classify(120.0, 100.0, higher_is_better=False)
    assert verdict.status == "regression"


def test_duration_drop_is_an_improvement():
    verdict = _classify(80.0, 100.0, higher_is_better=False)
    assert verdict.status == "improvement"


@given(
    baseline=st.floats(min_value=10, max_value=10_000, allow_nan=False),
    delta_pct=st.floats(min_value=10, max_value=100, allow_nan=False),
    higher_is_better=st.booleans(),
)
def test_direction_invariant_worse_side_is_always_regression(baseline, delta_pct, higher_is_better):
    """Core anti-inversion property: whichever sign is 'worse' for the
    metric's OWN direction always classifies `regression` once floor and
    threshold are cleared — symmetric across BOTH directions."""
    sign = -1 if higher_is_better else 1
    latest = baseline * (1 + sign * delta_pct / 100)
    verdict = _classify(
        latest, baseline, higher_is_better=higher_is_better, floor=1.0, threshold_pct=5.0
    )
    if abs(latest - baseline) >= 1.0:
        assert verdict.status == "regression"


@given(
    baseline=st.floats(min_value=10, max_value=10_000, allow_nan=False),
    delta_pct=st.floats(min_value=10, max_value=100, allow_nan=False),
    higher_is_better=st.booleans(),
)
def test_direction_invariant_better_side_is_always_improvement(
    baseline, delta_pct, higher_is_better
):
    sign = 1 if higher_is_better else -1
    latest = baseline * (1 + sign * delta_pct / 100)
    verdict = _classify(
        latest, baseline, higher_is_better=higher_is_better, floor=1.0, threshold_pct=5.0
    )
    if abs(latest - baseline) >= 1.0:
        assert verdict.status == "improvement"


# ===== floor + threshold gating (spec "Threshold and Absolute Floor") =====


def test_below_absolute_floor_stays_stable_even_if_pct_exceeds_threshold():
    verdict = _classify(100.4, 100.0, higher_is_better=False, floor=5.0, threshold_pct=0.1)
    assert verdict.status == "stable"


def test_below_threshold_pct_stays_stable_even_if_floor_exceeded():
    verdict = _classify(200.0, 100.0, higher_is_better=False, floor=5.0, threshold_pct=500.0)
    assert verdict.status == "stable"


def test_boundary_exactly_at_threshold_and_floor_flags():
    """Both gates are inclusive (`>=`) — exactly-at-threshold flags."""
    verdict = _classify(105.0, 100.0, higher_is_better=False, floor=5.0, threshold_pct=5.0)
    assert verdict.status == "regression"


@given(st.floats(min_value=-4.9, max_value=4.9, allow_nan=False))
def test_floor_suppresses_all_sub_floor_deltas(delta):
    baseline = 100.0
    latest = baseline + delta
    verdict = _classify(latest, baseline, higher_is_better=False, floor=5.0, threshold_pct=0.0)
    assert verdict.status == "stable"


# ===== insufficient-data / corner cases C1, C3, C4, C5 (never silent stable) =====


def test_c1_first_ever_run_no_baseline_is_insufficient_data():
    """C1: known flow's first-ever run has no prior baseline."""
    verdict = _classify(100.0, None, higher_is_better=False)
    assert verdict.status == "insufficient-data"


def test_c5_new_metric_absent_from_baseline_is_insufficient_data():
    """C5: metric present in the latest run but absent from every baseline
    commit — the analyzer passes `baseline=None` for that metric."""
    verdict = _classify(42.0, None, higher_is_better=True)
    assert verdict.status == "insufficient-data"


def test_latest_none_is_insufficient_data():
    verdict = _classify(None, 100.0, higher_is_better=False)
    assert verdict.status == "insufficient-data"


def test_c3_single_baseline_commit_is_insufficient_data():
    verdict = _classify(100.0, 100.0, higher_is_better=False, baseline_commit_n=1, min_n=3)
    assert verdict.status == "insufficient-data"


def test_too_few_post_warmup_samples_is_insufficient_data():
    verdict = _classify(100.0, 100.0, higher_is_better=False, sample_n=1, min_n=3)
    assert verdict.status == "insufficient-data"


def test_c4_all_equal_nonzero_baseline_is_stable_no_crash():
    verdict = _classify(100.0, 100.0, higher_is_better=False)
    assert verdict.status == "stable"


def test_c4_all_equal_zero_baseline_is_stable_no_divide_by_zero():
    verdict = _classify(0.0, 0.0, higher_is_better=False)
    assert verdict.status == "stable"


def test_baseline_zero_nonzero_delta_does_not_crash():
    verdict = _classify(5.0, 0.0, higher_is_better=False, floor=1.0, threshold_pct=5.0)
    assert verdict.status in {"regression", "improvement", "stable"}


@given(
    latest=st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False)),
    baseline=st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False)),
    higher_is_better=st.booleans(),
)
def test_classify_never_raises(latest, baseline, higher_is_better):
    classify(
        "metric",
        latest,
        baseline,
        unit="ms",
        higher_is_better=higher_is_better,
        threshold_pct=5.0,
        floor=5.0,
        baseline_commit_n=5,
        sample_n=5,
        min_n=3,
    )


def test_verdict_carries_identity_and_series_fields_through():
    verdict = classify(
        "fps_avg",
        54.0,
        60.0,
        unit="fps",
        higher_is_better=True,
        threshold_pct=5.0,
        floor=2.0,
        baseline_commit_n=8,
        sample_n=10,
        min_n=3,
        series=(58.0, 59.0, 60.0, 54.0),
    )
    assert verdict.metric_name == "fps_avg"
    assert verdict.unit == "fps"
    assert verdict.latest_value == 54.0
    assert verdict.baseline_value == 60.0
    assert verdict.sample_n == 10
    assert verdict.baseline_commit_n == 8
    assert verdict.series == (58.0, 59.0, 60.0, 54.0)
    assert verdict.status == "regression"
    assert verdict.floor == 2.0


def test_verdict_carries_the_active_floor_even_when_insufficient_data():
    """The `--json` contract (PR-C `contracts/compare_v1.py`) needs the
    ACTIVE floor per-metric even when no verdict could be classified — the
    floor is CONFIG-derived, not a symptom of enough/not-enough history, so
    it must thread through on every path, including `insufficient-data`."""
    verdict = classify(
        "checkout",
        100.0,
        None,  # no baseline -> insufficient-data
        unit="ms",
        higher_is_better=False,
        threshold_pct=5.0,
        floor=7.5,
        baseline_commit_n=0,
        sample_n=3,
        min_n=3,
    )
    assert verdict.status == "insufficient-data"
    assert verdict.floor == 7.5


# ===== `series_points` threading (budget-check design §5, task 1.3) =====


def test_classify_echoes_series_points_onto_verdict_normal_path():
    points = (SeriesPoint(commit="c1", value=58.0), SeriesPoint(commit="HEAD", value=54.0))
    verdict = classify(
        "fps_avg",
        54.0,
        60.0,
        unit="fps",
        higher_is_better=True,
        threshold_pct=5.0,
        floor=2.0,
        baseline_commit_n=8,
        sample_n=10,
        min_n=3,
        series_points=points,
    )
    assert verdict.status == "regression"  # normal classification path
    assert verdict.series_points == points


def test_classify_echoes_series_points_onto_verdict_insufficient_data_path():
    points = (SeriesPoint(commit="a", value=1.0),)
    verdict = classify(
        "metric",
        100.0,
        None,  # no baseline -> insufficient-data early return
        unit="ms",
        higher_is_better=False,
        threshold_pct=5.0,
        floor=5.0,
        baseline_commit_n=0,
        sample_n=3,
        min_n=3,
        series_points=points,
    )
    assert verdict.status == "insufficient-data"
    assert verdict.series_points == points


def test_classify_series_points_defaults_to_empty_tuple_when_omitted():
    verdict = _classify(100.0, 100.0, higher_is_better=False)
    assert verdict.series_points == ()


# ===== `higher_is_better` threading onto `Verdict` (audit fix: the
# `--json` contract's `direction` must read this field, not re-derive it
# by metric name at serialization time) =====


def test_classify_echoes_higher_is_better_onto_verdict_normal_path():
    verdict = _classify(54.0, 60.0, higher_is_better=True)
    assert verdict.status == "regression"
    assert verdict.higher_is_better is True


def test_classify_echoes_higher_is_better_false_onto_verdict_normal_path():
    verdict = _classify(120.0, 100.0, higher_is_better=False)
    assert verdict.status == "regression"
    assert verdict.higher_is_better is False


def test_classify_echoes_higher_is_better_onto_verdict_insufficient_data_path():
    verdict = _classify(100.0, None, higher_is_better=True)
    assert verdict.status == "insufficient-data"
    assert verdict.higher_is_better is True
