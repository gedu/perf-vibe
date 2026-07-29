"""Pure hypothesis-hardened tests for `perf.domain.statistics` (design
"Median location" decision — two-level median, no SQL `MEDIAN` aggregate;
tasks 1.1 RED / 1.2 GREEN). No I/O — pure aggregation math only.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from perf.domain.statistics import median, median_by_commit, percentile, robust_noise

# ===== median =====


def test_median_odd_count_returns_middle_value():
    assert median([1.0, 5.0, 3.0]) == 3.0


def test_median_even_count_returns_average_of_two_middle():
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_median_single_value_returns_that_value():
    assert median([42.0]) == 42.0


def test_median_empty_raises_value_error():
    with pytest.raises(ValueError):
        median([])


@given(
    st.lists(
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        min_size=1,
        max_size=50,
    )
)
def test_median_is_within_min_max_bounds(values):
    result = median(values)
    assert min(values) <= result <= max(values)


def test_median_none_value_raises_value_error_not_type_error():
    """FIX 1 (BLOCKER, defensive domain guard, PR-B review): a `None`
    slipping into the input (e.g. an unfiltered NULL `p90_ms` from an
    n=1 run) must raise a clear `ValueError`, never a bare `TypeError`
    from `sorted()` comparing `None` to `float` — callers (the analyzer)
    are expected to filter `None`s before calling; this keeps the domain
    function strict as a defensive backstop."""
    with pytest.raises(ValueError):
        median([1.0, None, 3.0])  # type: ignore[list-item]


# ===== percentile (nearest-rank, matches `run_metric_summary`'s p90_ms) =====


def test_percentile_single_value_returns_it_for_any_p():
    assert percentile([7.0], 50) == 7.0
    assert percentile([7.0], 90) == 7.0
    assert percentile([7.0], 0) == 7.0


def test_percentile_all_equal_returns_that_value():
    assert percentile([3.0, 3.0, 3.0], 90) == 3.0


def test_percentile_empty_raises_value_error():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_percentile_rejects_out_of_range_p():
    with pytest.raises(ValueError):
        percentile([1.0, 2.0], 150)


@given(
    st.lists(
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        min_size=1,
        max_size=50,
    ),
)
def test_percentile_min_p50_p90_max_invariant(values):
    p50 = percentile(values, 50)
    p90 = percentile(values, 90)
    assert min(values) <= p50 <= p90 <= max(values)


# ===== robust_noise (1.4826 * MAD — adaptive noise floor, Task 2) =====


def test_robust_noise_empty_is_zero():
    assert robust_noise([]) == 0.0


def test_robust_noise_single_value_is_zero():
    assert robust_noise([42.0]) == 0.0


def test_robust_noise_zero_variance_is_zero():
    assert robust_noise([7.0, 7.0, 7.0, 7.0]) == 0.0


def test_robust_noise_two_values_is_well_defined():
    # median([1, 3]) == 2; deviations [1, 1]; MAD == 1; 1.4826 * 1.
    assert robust_noise([1.0, 3.0]) == pytest.approx(1.4826)


def test_robust_noise_known_example_scales_mad_by_1_4826():
    # median([100, 90, 110, 95, 105]) == 100; abs deviations [0,10,10,5,5];
    # median of those == 5; robust noise == 1.4826 * 5.
    assert robust_noise([100.0, 90.0, 110.0, 95.0, 105.0]) == pytest.approx(1.4826 * 5.0)


def test_robust_noise_is_outlier_robust():
    """One freak run cannot inflate the estimate the way a stddev would —
    that is the whole point of using the MAD for the noise floor."""
    calm = [100.0, 101.0, 99.0, 100.0, 101.0]
    with_outlier = [*calm, 100_000.0]
    assert robust_noise(with_outlier) == pytest.approx(robust_noise(calm), rel=0.5)


@given(
    st.lists(
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        min_size=0,
        max_size=50,
    )
)
def test_robust_noise_is_non_negative_and_never_nan(values):
    result = robust_noise(values)
    assert not math.isnan(result)
    assert result >= 0.0


# ===== median_by_commit (spec "Baseline Correctness" — repeated same-commit
# runs collapse to exactly ONE median point) =====


def test_median_by_commit_collapses_repeated_commit_runs():
    points = [("c1", 10.0), ("c1", 20.0), ("c1", 30.0), ("c2", 100.0)]
    result = median_by_commit(points)
    assert result == {"c1": 20.0, "c2": 100.0}


def test_median_by_commit_empty_returns_empty_mapping():
    assert median_by_commit([]) == {}


@given(
    st.dictionaries(
        st.text(min_size=1, max_size=8),
        st.lists(
            st.floats(allow_nan=False, allow_infinity=False, width=32),
            min_size=1,
            max_size=5,
        ),
        min_size=1,
        max_size=10,
    )
)
def test_median_by_commit_yields_exactly_one_point_per_commit(commit_values):
    points = [(commit, value) for commit, values in commit_values.items() for value in values]
    result = median_by_commit(points)
    assert set(result) == set(commit_values)
    for commit, values in commit_values.items():
        assert result[commit] == median(values)
