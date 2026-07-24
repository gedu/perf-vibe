"""Golden tests for `cli/output/budget_check_pretty` — budget-check's OWN
renderer (design §9, decision D2; tasks 3.1/3.3/3.5). Color forced off,
fixed width, byte-identical on repeat render, mirroring
`test_compare_pretty_golden.py`'s discipline. `compare_pretty.py` stays
frozen and is NEVER imported here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from fakes import FakeCommitLog, make_run_context  # noqa: E402
from perf.cli.output.budget_check_pretty import render_metric_detail, render_summary  # noqa: E402
from perf.domain.calibration import CalibrationReport, MetricCalibration  # noqa: E402
from perf.domain.model import (  # noqa: E402
    GATE_FAIL,
    GATE_PASS,
    GATE_SKIPPED,
    BudgetVerdict,
    GatedVerdict,
    SeriesPoint,
    Verdict,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_ANSI_ESCAPE = "\x1b["


def _assert_or_update_golden(request, fixture_name: str, actual: str) -> None:
    fixture_path = _FIXTURES_DIR / fixture_name
    if request.config.getoption("--update-golden"):
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(actual)
        return
    expected = fixture_path.read_text()
    assert actual == expected, (
        f"golden mismatch for {fixture_name} — run with --update-golden to "
        "regenerate if this change is intentional"
    )


def _reasonable_calibration(**overrides) -> CalibrationReport:
    defaults = {
        "metrics": (
            MetricCalibration(
                metric_name="checkout",
                status="reasonable",
                flagged_count=1,
                total_count=10,
                max_abs=20.0,
                noise_pct=1.0,
            ),
        ),
        "status": "reasonable",
        "runs_flagged": 1,
        "runs_total": 10,
    }
    defaults.update(overrides)
    return CalibrationReport(**defaults)


_RC = make_run_context(device_key="Pixel-7|14|physical", model="Pixel-7", git_commit="a1b2c3d")


def _checkout_regression_verdict() -> Verdict:
    return Verdict(
        metric_name="checkout",
        delta_pct=20.0,
        threshold_pct=5.0,
        status="regression",
        latest_value=120.0,
        baseline_value=100.0,
        unit="ms",
        sample_n=3,
        baseline_commit_n=10,
        series=(98.0, 100.0, 101.0, 120.0),
        floor=8.0,
        series_points=(
            SeriesPoint(commit="d4e5f6a", value=98.0),
            SeriesPoint(commit="e5f6a7b", value=100.0),
            SeriesPoint(commit="f6a7b8c", value=101.0),
            SeriesPoint(commit="a1b2c3d", value=120.0),
        ),
    )


def _fps_stable_verdict() -> Verdict:
    return Verdict(
        metric_name="fps_avg",
        delta_pct=-3.3,
        threshold_pct=5.0,
        status="stable",
        latest_value=58.0,
        baseline_value=60.0,
        unit="fps",
        sample_n=3,
        baseline_commit_n=10,
        series=(60.0, 59.0, 59.5, 58.0),
        floor=2.0,
    )


def _fail_verdict() -> BudgetVerdict:
    return BudgetVerdict(
        gate_status=GATE_FAIL,
        gated_verdicts=(
            GatedVerdict(verdict=_checkout_regression_verdict(), gated=True),
            GatedVerdict(verdict=_fps_stable_verdict(), gated=False),
        ),
        offending_metrics=("checkout",),
        strict=False,
        calibration=_reasonable_calibration(),
    )


def _pass_verdict() -> BudgetVerdict:
    stable_checkout = Verdict(
        metric_name="checkout",
        delta_pct=2.0,
        threshold_pct=5.0,
        status="stable",
        latest_value=102.0,
        baseline_value=100.0,
        unit="ms",
        sample_n=3,
        baseline_commit_n=10,
        series=(98.0, 100.0, 101.0, 102.0),
        floor=8.0,
    )
    return BudgetVerdict(
        gate_status=GATE_PASS,
        gated_verdicts=(
            GatedVerdict(verdict=stable_checkout, gated=False),
            GatedVerdict(verdict=_fps_stable_verdict(), gated=False),
        ),
        offending_metrics=(),
        strict=False,
        calibration=_reasonable_calibration(),
    )


def _skipped_verdict() -> BudgetVerdict:
    insufficient = Verdict(
        metric_name="checkout",
        delta_pct=0.0,
        threshold_pct=5.0,
        status="insufficient-data",
        latest_value=100.0,
        baseline_value=None,
        unit="ms",
        sample_n=1,
        baseline_commit_n=0,
        series=(100.0,),
        floor=8.0,
    )
    return BudgetVerdict(
        gate_status=GATE_SKIPPED,
        gated_verdicts=(GatedVerdict(verdict=insufficient, gated=False),),
        offending_metrics=(),
        strict=False,
        calibration=CalibrationReport(
            metrics=(), status="insufficient-data", runs_flagged=0, runs_total=0
        ),
    )


# ===== summary PASS/FAIL/SKIPPED — golden + structural invariants =====


def test_summary_fail_matches_golden(request):
    actual = render_summary(
        _fail_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    _assert_or_update_golden(request, "budget_check_summary_fail.txt", actual)


def test_summary_pass_matches_golden(request):
    actual = render_summary(
        _pass_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    _assert_or_update_golden(request, "budget_check_summary_pass.txt", actual)


def test_summary_skipped_matches_golden(request):
    actual = render_summary(
        _skipped_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    _assert_or_update_golden(request, "budget_check_summary_skipped.txt", actual)


def test_summary_renders_deterministically_twice_in_a_row():
    bv = _fail_verdict()
    first = render_summary(bv, _RC, FakeCommitLog(), flow_name="checkout", color=False)
    second = render_summary(bv, _RC, FakeCommitLog(), flow_name="checkout", color=False)
    assert first == second


def test_summary_has_no_ansi_escapes_when_color_off():
    for bv in (_fail_verdict(), _pass_verdict(), _skipped_verdict()):
        actual = render_summary(bv, _RC, FakeCommitLog(), flow_name="checkout", color=False)
        assert _ANSI_ESCAPE not in actual


def test_summary_open_right_frame_no_right_border():
    """Open-right layout law: top rule, bottom rule, left rail `│` only —
    NEVER a right border (wide glyphs desync monospace alignment)."""
    for bv in (_fail_verdict(), _pass_verdict(), _skipped_verdict()):
        actual = render_summary(bv, _RC, FakeCommitLog(), flow_name="checkout", color=False)
        lines = actual.rstrip("\n").split("\n")
        assert lines[0].startswith("┌─")
        assert lines[-1] == "└─"
        for line in lines[1:-1]:
            assert line == "" or line.startswith("│") or line.startswith("├")
        # No line ends in a box-drawing right border character.
        assert not any(line.rstrip().endswith(("┐", "┘", "┤")) for line in lines)


def test_summary_blank_line_between_metric_rows():
    actual = render_summary(
        _fail_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    assert "checkout" in actual
    assert "fps_avg" in actual
    lines = actual.split("\n")
    checkout_line_idx = next(i for i, line in enumerate(lines) if "120.0" in line)
    fps_line_idx = next(i for i, line in enumerate(lines) if "fps_avg" in line and "58.0" in line)
    assert fps_line_idx > checkout_line_idx + 1  # at least one blank line between


def test_summary_regression_legible_via_glyph_and_status_word_alone():
    actual = render_summary(
        _fail_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    assert "✗" in actual
    assert "REGRESSION" in actual


def test_summary_pass_gate_banner_shows_pass_glyph_and_word():
    actual = render_summary(
        _pass_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    assert "✓" in actual
    assert "GATE PASSED" in actual
    assert "exit 0" in actual


def test_summary_fail_gate_banner_shows_fail_glyph_and_exit_1():
    actual = render_summary(
        _fail_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    assert "✗" in actual
    assert "GATE FAILED" in actual
    assert "exit 1" in actual


def test_summary_skipped_gate_banner_shows_skipped_glyph_and_exit_0():
    actual = render_summary(
        _skipped_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    assert "GATE SKIPPED" in actual
    assert "exit 0" in actual


def test_summary_header_shows_head_short_sha_and_branch():
    actual = render_summary(
        _fail_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    assert "HEAD" in actual
    assert _RC.git_commit[:7] in actual
    assert _RC.git_branch in actual


# ===== --verbose auto-expand =====


def test_verbose_auto_expands_regressed_metric_only():
    commit_log = FakeCommitLog(subject="feat: coupon field on checkout")
    actual = render_summary(
        _fail_verdict(), _RC, commit_log, flow_name="checkout", verbose=True, color=False
    )
    assert "feat: coupon field on checkout" in actual


def test_verbose_calls_commit_log_exactly_once_for_multiple_regressions():
    checkout = _checkout_regression_verdict()
    other_regression = Verdict(
        metric_name="startup_p50",
        delta_pct=15.0,
        threshold_pct=5.0,
        status="regression",
        latest_value=115.0,
        baseline_value=100.0,
        unit="ms",
        sample_n=3,
        baseline_commit_n=10,
        series=(100.0, 101.0, 115.0),
        floor=8.0,
    )
    bv = BudgetVerdict(
        gate_status=GATE_FAIL,
        gated_verdicts=(
            GatedVerdict(verdict=checkout, gated=True),
            GatedVerdict(verdict=other_regression, gated=True),
        ),
        offending_metrics=("checkout", "startup_p50"),
        strict=False,
        calibration=_reasonable_calibration(),
    )
    commit_log = FakeCommitLog(subject="fixed subject")
    render_summary(bv, _RC, commit_log, flow_name="checkout", verbose=True, color=False)
    assert commit_log.calls == [_RC.git_commit]


def test_non_verbose_does_not_expand_regressed_metric():
    commit_log = FakeCommitLog(subject="should not appear")
    actual = render_summary(
        _fail_verdict(), _RC, commit_log, flow_name="checkout", verbose=False, color=False
    )
    assert "should not appear" not in actual
    assert commit_log.calls == []


# ===== --metric detail view =====


def test_detail_view_matches_golden(request):
    commit_log = FakeCommitLog(subject="feat: coupon field on checkout")
    actual = render_metric_detail(
        _fail_verdict(),
        "checkout",
        _RC,
        commit_log,
        flow_name="checkout",
        mode="warm",
        color=False,
    )
    _assert_or_update_golden(request, "budget_check_detail_checkout.txt", actual)


def test_detail_view_shows_y_axis_ticks_and_x_axis_commit_labels():
    actual = render_metric_detail(
        _fail_verdict(),
        "checkout",
        _RC,
        FakeCommitLog(),
        flow_name="checkout",
        mode="warm",
        color=False,
    )
    assert "┤" in actual  # y-axis tick marker
    assert "d4e5f6a"[:7] in actual  # x-axis commit label


def test_detail_view_marks_head_column():
    actual = render_metric_detail(
        _fail_verdict(),
        "checkout",
        _RC,
        FakeCommitLog(),
        flow_name="checkout",
        mode="warm",
        color=False,
    )
    assert "HEAD" in actual


def test_detail_view_shows_git_context_on_regression():
    commit_log = FakeCommitLog(subject="feat: coupon field on checkout")
    actual = render_metric_detail(
        _fail_verdict(), "checkout", _RC, commit_log, flow_name="checkout", mode="warm", color=False
    )
    assert "feat: coupon field on checkout" in actual
    assert commit_log.calls == [_RC.git_commit]


def test_detail_view_falls_back_to_sha_only_when_subject_unavailable():
    commit_log = FakeCommitLog(subject=None)
    actual = render_metric_detail(
        _fail_verdict(), "checkout", _RC, commit_log, flow_name="checkout", mode="warm", color=False
    )
    assert _RC.git_commit[:7] in actual  # still shows the sha
    # no crash, no exception — reaching this line proves fail-graceful.


def test_detail_view_empty_series_does_not_crash():
    no_series = Verdict(
        metric_name="lonely",
        delta_pct=0.0,
        threshold_pct=5.0,
        status="insufficient-data",
        latest_value=None,
        baseline_value=None,
        unit="ms",
        sample_n=0,
        baseline_commit_n=0,
        series=(),
        floor=5.0,
        series_points=(),
    )
    bv = BudgetVerdict(
        gate_status=GATE_SKIPPED,
        gated_verdicts=(GatedVerdict(verdict=no_series, gated=False),),
        offending_metrics=(),
        strict=False,
        calibration=CalibrationReport(
            metrics=(), status="insufficient-data", runs_flagged=0, runs_total=0
        ),
    )
    actual = render_metric_detail(
        bv, "lonely", _RC, FakeCommitLog(), flow_name="checkout", mode="warm", color=False
    )
    assert "lonely" in actual


def test_detail_view_single_point_series_does_not_crash():
    single = Verdict(
        metric_name="checkout",
        delta_pct=0.0,
        threshold_pct=5.0,
        status="stable",
        latest_value=100.0,
        baseline_value=100.0,
        unit="ms",
        sample_n=3,
        baseline_commit_n=3,
        series=(100.0,),
        floor=5.0,
        series_points=(SeriesPoint(commit="a1b2c3d", value=100.0),),
    )
    bv = BudgetVerdict(
        gate_status=GATE_PASS,
        gated_verdicts=(GatedVerdict(verdict=single, gated=False),),
        offending_metrics=(),
        strict=False,
        calibration=_reasonable_calibration(),
    )
    actual = render_metric_detail(
        bv, "checkout", _RC, FakeCommitLog(), flow_name="checkout", mode="warm", color=False
    )
    assert "checkout" in actual


def test_detail_view_zero_variance_series_does_not_crash():
    flat = Verdict(
        metric_name="checkout",
        delta_pct=0.0,
        threshold_pct=5.0,
        status="stable",
        latest_value=100.0,
        baseline_value=100.0,
        unit="ms",
        sample_n=3,
        baseline_commit_n=4,
        series=(100.0, 100.0, 100.0, 100.0),
        floor=5.0,
        series_points=(
            SeriesPoint(commit="c1", value=100.0),
            SeriesPoint(commit="c2", value=100.0),
            SeriesPoint(commit="c3", value=100.0),
            SeriesPoint(commit="a1b2c3d", value=100.0),
        ),
    )
    bv = BudgetVerdict(
        gate_status=GATE_PASS,
        gated_verdicts=(GatedVerdict(verdict=flat, gated=False),),
        offending_metrics=(),
        strict=False,
        calibration=_reasonable_calibration(),
    )
    actual = render_metric_detail(
        bv, "checkout", _RC, FakeCommitLog(), flow_name="checkout", mode="warm", color=False
    )
    assert "checkout" in actual


def test_detail_view_metric_absent_from_run_renders_no_data_message():
    actual = render_metric_detail(
        _fail_verdict(),
        "not-in-this-run",
        _RC,
        FakeCommitLog(),
        flow_name="checkout",
        mode="warm",
        color=False,
    )
    assert "no data" in actual.lower()


def test_detail_view_no_right_border():
    actual = render_metric_detail(
        _fail_verdict(),
        "checkout",
        _RC,
        FakeCommitLog(),
        flow_name="checkout",
        mode="warm",
        color=False,
    )
    lines = actual.rstrip("\n").split("\n")
    assert lines[0].startswith("┌─")
    assert lines[-1] == "└─"


def _summary_lines() -> tuple[str, list[str]]:
    """Header line plus the metric rows of a rendered FAIL summary."""

    rendered = render_summary(
        _fail_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    lines = rendered.rstrip("\n").split("\n")
    header = next(line for line in lines if "METRIC" in line)
    rows = [
        line
        for line in lines
        if line.startswith("│   ")
        and any(name in line for name in ("checkout", "fps_avg"))
        and "METRIC" not in line
    ]
    return header, rows


def test_summary_header_aligns_with_its_columns():
    """The header must sit over the column it labels.

    A golden file cannot catch this: it freezes whatever was rendered,
    misalignment included. This pins the relationship instead — left-aligned
    columns share a START offset with their title, right-aligned ones share
    an END offset. Before the shared column spec, the data rows carried a
    two-character status-glyph prefix the header did not, so every column
    after METRIC sat 2-8 characters off its own heading.
    """

    header, rows = _summary_lines()
    assert rows, "expected at least one metric row"

    for row in rows:
        name = "checkout" if "checkout" in row else "fps_avg"
        status = "REGRESSION" if "REGRESSION" in row else "stable"
        spark = next(char for char in row if char in "▁▂▃▄▅▆▇█")

        # Left-aligned columns: the cell begins where its title begins.
        assert row.index(name) == header.index("METRIC")
        assert row.index(status) == header.index("STATUS")
        assert row.index(spark) == header.index("TREND")

        # Right-aligned columns: the cell ends where its title ends. Both
        # values in a row carry a unit, so search past the metric name to
        # avoid matching the flow name in the box header.
        latest_end = row.index(" ms", row.index(name)) + 3 if " ms" in row else None
        if latest_end is not None:
            assert latest_end == header.index("LATEST") + len("LATEST")


def test_summary_rules_span_the_table_they_underline():
    """Both horizontal rules must reach the same right edge as the header.

    The first cut hardcoded a width of 74, which rendered the two rules 78
    and 76 characters wide — visibly ragged against each other. They are now
    derived from the column spec, so a column change cannot leave one short.
    """

    rendered = render_summary(
        _fail_verdict(), _RC, FakeCommitLog(), flow_name="checkout", color=False
    )
    lines = rendered.rstrip("\n").split("\n")
    header = next(line for line in lines if "METRIC" in line)
    column_rule = next(line for line in lines if line.startswith("│   ─"))
    gate_rule = next(line for line in lines if line.startswith("├─"))

    assert len(column_rule) == len(header)
    assert len(gate_rule) == len(header)
