"""Golden tests for `cli/output/compare_pretty.render_compare` (SKILL rule
8: "Golden files for pretty output with color forced off"). PR-C tasks
3.3/3.3a — five UX cases: (a) normal multi-metric verdict, (b) a
`regression` (plain-text emphasis marker present since color is off), (c)
`insufficient-data`, (d) a single-data-point sparkline, (e) the
`max == min` sparkline edge. Also asserts the sanity label appears in BOTH
pretty and `--json`, and that its presence never changes the exit code
(exit codes live at the CLI layer — `tests/integration/test_cli_compare.py`
— this file asserts the label text itself and NO ANSI leaking under
color-off).
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.compare_pretty import render_compare
from perf.contracts.compare_v1 import build_compare_payload
from perf.domain.calibration import CalibrationReport, MetricCalibration
from perf.domain.model import CompareResult, Verdict

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_ANSI_ESCAPE = "\x1b["


def _reasonable_calibration(**overrides) -> CalibrationReport:
    defaults = dict(
        metrics=(
            MetricCalibration(
                metric_name="checkout",
                status="reasonable",
                flagged_count=2,
                total_count=12,
                max_abs=30.0,
                noise_pct=1.2,
            ),
        ),
        status="reasonable",
        runs_flagged=2,
        runs_total=12,
    )
    defaults.update(overrides)
    return CalibrationReport(**defaults)


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


# ===== (a) normal multi-metric verdict =====


def _normal_result() -> CompareResult:
    verdicts = (
        Verdict(
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
            floor=5.0,
        ),
        Verdict(
            metric_name="fps_avg",
            delta_pct=8.0,
            threshold_pct=5.0,
            status="improvement",
            latest_value=64.8,
            baseline_value=60.0,
            unit="fps",
            sample_n=3,
            baseline_commit_n=10,
            series=(58.0, 59.0, 60.0, 64.8),
            floor=2.0,
        ),
    )
    return CompareResult(verdicts=verdicts, calibration=_reasonable_calibration())


def test_normal_multi_metric_verdict_matches_golden(request):
    actual = render_compare(_normal_result(), color=False)
    _assert_or_update_golden(request, "compare_normal.txt", actual)


def test_normal_multi_metric_verdict_has_no_ansi_escapes():
    actual = render_compare(_normal_result(), color=False)
    assert _ANSI_ESCAPE not in actual


def test_sanity_label_present_in_both_pretty_and_json():
    result = _normal_result()
    pretty = render_compare(result, color=False)
    payload = build_compare_payload(result)
    assert "reasonable" in pretty
    assert "2 of 12" in pretty
    assert payload["calibration"]["status"] == "reasonable"
    assert payload["calibration"]["runs_flagged"] == 2
    assert payload["calibration"]["runs_total"] == 12


# ===== (b) a regression — plain-text emphasis marker with color off =====


def _regression_result() -> CompareResult:
    verdicts = (
        Verdict(
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
            floor=5.0,
        ),
    )
    return CompareResult(
        verdicts=verdicts,
        calibration=CalibrationReport(
            metrics=(
                MetricCalibration(
                    metric_name="checkout",
                    status="reasonable",
                    flagged_count=1,
                    total_count=10,
                    max_abs=25.0,
                    noise_pct=1.0,
                ),
            ),
            status="reasonable",
            runs_flagged=1,
            runs_total=10,
        ),
    )


def test_regression_matches_golden(request):
    actual = render_compare(_regression_result(), color=False)
    _assert_or_update_golden(request, "compare_regression.txt", actual)


def test_regression_has_plain_text_emphasis_marker_with_color_off():
    actual = render_compare(_regression_result(), color=False)
    assert "!" in actual
    assert "REGRESSION" in actual
    assert _ANSI_ESCAPE not in actual


# ===== (c) insufficient-data =====


def _insufficient_data_result() -> CompareResult:
    verdicts = (
        Verdict(
            metric_name="checkout",
            delta_pct=0.0,
            threshold_pct=5.0,
            status="insufficient-data",
            latest_value=100.0,
            baseline_value=None,
            unit="ms",
            sample_n=3,
            baseline_commit_n=0,
            series=(100.0,),
            floor=5.0,
        ),
    )
    return CompareResult(
        verdicts=verdicts,
        calibration=CalibrationReport(
            metrics=(), status="insufficient-data", runs_flagged=0, runs_total=0
        ),
    )


def test_insufficient_data_matches_golden(request):
    actual = render_compare(_insufficient_data_result(), color=False)
    _assert_or_update_golden(request, "compare_insufficient_data.txt", actual)


def test_insufficient_data_shows_classification_and_no_crash():
    actual = render_compare(_insufficient_data_result(), color=False)
    assert "INSUFFICIENT-DATA" in actual
    assert "insufficient data" in actual.lower()


# ===== (d) single-data-point sparkline =====


def _single_point_result() -> CompareResult:
    verdicts = (
        Verdict(
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
        ),
    )
    return CompareResult(verdicts=verdicts, calibration=_reasonable_calibration())


def test_single_point_sparkline_matches_golden(request):
    actual = render_compare(_single_point_result(), color=False)
    _assert_or_update_golden(request, "compare_single_point.txt", actual)


def test_single_point_sparkline_does_not_crash():
    # A one-element series must render one sparkline glyph, no ZeroDivisionError.
    actual = render_compare(_single_point_result(), color=False)
    assert "checkout" in actual


# ===== (e) max == min sparkline edge (zero variance) =====


def _max_eq_min_result() -> CompareResult:
    verdicts = (
        Verdict(
            metric_name="checkout",
            delta_pct=0.0,
            threshold_pct=5.0,
            status="stable",
            latest_value=100.0,
            baseline_value=100.0,
            unit="ms",
            sample_n=3,
            baseline_commit_n=10,
            series=(100.0, 100.0, 100.0, 100.0),
            floor=5.0,
        ),
    )
    return CompareResult(verdicts=verdicts, calibration=_reasonable_calibration())


def test_max_eq_min_sparkline_matches_golden(request):
    actual = render_compare(_max_eq_min_result(), color=False)
    _assert_or_update_golden(request, "compare_max_eq_min.txt", actual)


def test_max_eq_min_sparkline_does_not_crash_or_divide_by_zero():
    actual = render_compare(_max_eq_min_result(), color=False)
    assert "checkout" in actual
