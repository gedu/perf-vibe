"""Unit tests for the shared terminal-rendering primitives
(`perf.cli.output.primitives`) — the vocabulary five pretty renderers draw
with after it was extracted out of their five private copies.

These tests exist to pin the CONTRACT the extraction had to preserve, so a
later restyle of `compare`/`history`/`reassure-import` cannot quietly change
what every other view renders. Two of them guard a real trap found during the
extraction:

  * `format_value` merged a 1-arg (`compare`/`history`) and a 2-arg
    (`budget-check`) helper that shared a name but not a signature. The merged
    superset must reproduce BOTH shapes exactly — including an empty unit,
    which is why the omit test is `unit is None` and not "unit is falsy".
  * `sparkline` merged two independently written copies. The edges
    (empty/single-point/zero-variance) are where a divide-by-zero or a
    length change would hide, so each gets its own reason to fail.

No I/O and no renderer involved: these are pure `value -> str` functions.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from perf.cli.output import primitives
from perf.domain.model import Verdict
from perf.domain.regression import (
    STATUS_INSUFFICIENT_DATA,
    STATUS_REGRESSION,
    STATUS_STABLE,
)

# ===== style =====


def test_style_wraps_text_in_code_and_reset_when_color_on():
    assert primitives.style("hi", color=True, code=primitives.BOLD) == "\x1b[1mhi\x1b[0m"


def test_style_emits_no_ansi_at_all_when_color_off():
    styled = primitives.style("hi", color=False, code=primitives.BOLD_RED)

    assert styled == "hi"
    assert "\x1b" not in styled


# ===== sparkline =====


def test_sparkline_of_empty_series_is_empty():
    assert primitives.sparkline([]) == ""


def test_sparkline_of_single_point_is_the_lowest_level():
    # One point has no range to normalize against, so it renders the floor
    # rather than an arbitrary middle.
    assert primitives.sparkline([42.0]) == primitives.SPARK_CHARS[0]


def test_sparkline_of_zero_variance_series_is_the_flat_middle_level():
    flat = primitives.SPARK_CHARS[len(primitives.SPARK_CHARS) // 2]

    assert primitives.sparkline([7.0, 7.0, 7.0]) == flat * 3


def test_sparkline_of_normal_series_spans_lowest_to_highest_level():
    spark = primitives.sparkline([0.0, 50.0, 100.0])

    assert spark[0] == primitives.SPARK_CHARS[0]
    assert spark[-1] == primitives.SPARK_CHARS[-1]


@given(
    st.lists(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=40,
    )
)
def test_sparkline_renders_exactly_one_block_char_per_point(series):
    """One glyph per data point, always drawn from the known ramp — the
    invariant a column-aligned view depends on. Property-based because the
    interesting inputs are the degenerate ones (all-equal, single point,
    negatives) that examples keep missing."""

    spark = primitives.sparkline(series)

    assert len(spark) == len(series)
    assert set(spark) <= set(primitives.SPARK_CHARS)


# ===== format_value =====


def test_format_value_without_unit_omits_it_entirely():
    """The `compare`/`history` call shape."""

    assert primitives.format_value(120.04) == "120.0"


def test_format_value_with_unit_appends_it_after_a_space():
    """The `budget-check` call shape."""

    assert primitives.format_value(120.04, "ms") == "120.0 ms"


def test_format_value_with_empty_unit_keeps_the_trailing_space():
    # `budget-check` always passed a unit, so an EMPTY unit rendered a
    # trailing space. The superset must reproduce that, which is only true
    # while the omit test is `unit is None` rather than a falsy check.
    assert primitives.format_value(120.0, "") == "120.0 "


def test_format_value_of_none_is_a_dash_in_both_call_shapes():
    assert primitives.format_value(None) == "-"
    assert primitives.format_value(None, "ms") == "-"


# ===== arrow_and_pct =====


def _verdict(delta_pct: float, status: str = STATUS_STABLE) -> Verdict:
    return Verdict(metric_name="checkout", delta_pct=delta_pct, threshold_pct=5.0, status=status)


def test_arrow_and_pct_marks_a_rise_with_up_and_an_explicit_plus_sign():
    assert primitives.arrow_and_pct(_verdict(12.34, STATUS_REGRESSION)) == (
        primitives.ARROW_UP,
        "+12.3%",
    )


def test_arrow_and_pct_marks_a_fall_with_down_and_the_native_minus_sign():
    assert primitives.arrow_and_pct(_verdict(-8.0)) == (primitives.ARROW_DOWN, "-8.0%")


def test_arrow_and_pct_marks_no_movement_as_flat_and_signed_zero():
    assert primitives.arrow_and_pct(_verdict(0.0)) == (primitives.ARROW_FLAT, "+0.0%")


def test_arrow_and_pct_reports_insufficient_data_rather_than_a_fabricated_zero():
    # Without a baseline there is no delta to draw, so the view must show a
    # blank pair instead of implying the metric held steady.
    assert primitives.arrow_and_pct(_verdict(0.0, STATUS_INSUFFICIENT_DATA)) == (
        primitives.ARROW_NONE,
        "-",
    )
