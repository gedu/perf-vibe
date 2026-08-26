"""Golden tests for `cli/output/compare_pretty.render_compare` (SKILL rule
8: "Golden files for pretty output with color forced off"). PR-C tasks
3.3/3.3a — six UX cases: (a) normal multi-metric verdict, (b) a
`regression` (plain-text emphasis marker present since color is off), (c)
`insufficient-data`, (d) a single-data-point sparkline, (e) the
`max == min` sparkline edge, (f) the excluded-runs note. Also asserts the
sanity label appears in BOTH pretty and `--json`, and that its presence
never changes the exit code (exit codes live at the CLI layer —
`tests/integration/test_cli_compare.py` — this file asserts the label text
itself and NO ANSI leaking under color-off).

Sections (g)-(i) guard the restyle onto budget-check's table shape, and they
are deliberately NOT golden assertions: a golden proves the output is stable,
never that it is right, so the properties the restyle exists for get their own
named reasons to fail. (g) the box, the labelled columns, and the rule that
ONLY the glyph/Δ/STATUS carry color; (h) the `min→max` scale column across
every series edge, where the risk is a fabricated range rather than a crash;
(i) the device label in the box header.
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.compare_pretty import render_compare
from perf.cli.output.primitives import (
    BOLD_GREEN,
    BOLD_RED,
    DIM,
    GLYPH_NEUTRAL,
    GLYPH_OFFENDER,
    GLYPH_OK,
    RESET,
)
from perf.contracts.compare_v1 import build_compare_payload
from perf.domain.calibration import CalibrationReport, MetricCalibration
from perf.domain.model import CompareResult, Verdict

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_ANSI_ESCAPE = "\x1b["

# Fixed box-header context for every golden: `render_compare` needs the flow,
# mode and device to draw its `┌─` header, and a golden must not vary with the
# machine that runs it.
_FLOW = "demo"
_MODE = "warm"
_DEVICE_KEY = "Pixel 8 Pro|Android 14|physical"


def _render(result: CompareResult, *, color: bool = False) -> str:
    return render_compare(result, flow_name=_FLOW, mode=_MODE, device_key=_DEVICE_KEY, color=color)


def _reasonable_calibration(**overrides) -> CalibrationReport:
    defaults = {
        "metrics": (
            MetricCalibration(
                metric_name="checkout",
                status="reasonable",
                flagged_count=2,
                total_count=12,
                max_abs=30.0,
                noise_pct=1.2,
            ),
        ),
        "status": "reasonable",
        "runs_flagged": 2,
        "runs_total": 12,
    }
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
    actual = _render(_normal_result())
    _assert_or_update_golden(request, "compare_normal.txt", actual)


def test_normal_multi_metric_verdict_has_no_ansi_escapes():
    actual = _render(_normal_result())
    assert _ANSI_ESCAPE not in actual


def test_sanity_label_present_in_both_pretty_and_json():
    result = _normal_result()
    pretty = _render(result)
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
    actual = _render(_regression_result())
    _assert_or_update_golden(request, "compare_regression.txt", actual)


def test_regression_has_plain_text_emphasis_marker_with_color_off():
    """The emphasis marker is now the `✗` glyph column rather than a leading
    `!`, and it rides ALONGSIDE the uppercased status word — so a regression
    stays unmistakable with color off, which is the property this test
    guards (not the specific character)."""

    actual = _render(_regression_result())
    assert GLYPH_OFFENDER in actual
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
    actual = _render(_insufficient_data_result())
    _assert_or_update_golden(request, "compare_insufficient_data.txt", actual)


def test_insufficient_data_shows_classification_and_no_crash():
    """Only a `regression` is uppercased now (mirroring budget-check, which
    uppercases exactly its gate offenders), so the classification reads
    `insufficient-data` — still shown, still spelled out, no longer
    shouting."""

    actual = _render(_insufficient_data_result())
    assert "insufficient-data" in actual
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
    actual = _render(_single_point_result())
    _assert_or_update_golden(request, "compare_single_point.txt", actual)


def test_single_point_sparkline_does_not_crash():
    # A one-element series must render one sparkline glyph, no ZeroDivisionError.
    actual = _render(_single_point_result())
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
    actual = _render(_max_eq_min_result())
    _assert_or_update_golden(request, "compare_max_eq_min.txt", actual)


def test_max_eq_min_sparkline_does_not_crash_or_divide_by_zero():
    actual = _render(_max_eq_min_result())
    assert "checkout" in actual


# ===== (f) excluded-runs diagnostic note (anti-false-positive batch, Task 4):
# ONE dim line explaining runs the baseline silently dropped, pretty-only. =====


def _excluded_note_result() -> CompareResult:
    return CompareResult(
        verdicts=_insufficient_data_result().verdicts,
        calibration=CalibrationReport(
            metrics=(), status="insufficient-data", runs_flagged=0, runs_total=0
        ),
        excluded_same_commit=3,
        excluded_no_commit=2,
    )


def test_excluded_note_matches_golden(request):
    actual = _render(_excluded_note_result())
    _assert_or_update_golden(request, "compare_excluded_note.txt", actual)


def _unwrapped(rendered: str) -> str:
    """Collapses the box rail and the note's own line wrapping back into one
    flat string, so an assertion about the note's WORDING does not also assert
    WHERE the wrap fell.

    These assertions read phrases that straddle a wrap point: the note is
    wrapped to `_NOTE_TEXT_WIDTH` so the box keeps its `│` rail on every line
    instead of letting the terminal wrap a 128-column line and lose the rail.
    Before this helper, `"2 without a git commit"` failed purely because the
    text had become `2 without a` / `│   git commit`, which says nothing about
    whether the message is right. Now the wrap width can change freely and only
    a real wording change breaks a test."""

    return " ".join(rendered.replace("│", " ").split())


def test_excluded_note_present_and_counts_rendered_color_off():
    actual = _render(_excluded_note_result())
    flat = _unwrapped(actual)
    assert "5 run(s) excluded from baseline" in flat  # 3 + 2
    assert "3 on the current commit" in flat
    assert "2 without a git commit" in flat
    assert "commit your changes to grow history" in flat
    assert _ANSI_ESCAPE not in actual  # dim styling never leaks under color-off


def test_excluded_note_keeps_the_box_rail_on_every_wrapped_line():
    """The note is the longest line this view emits — 128 columns with several
    excluded runs. Left to the terminal, it wraps and every continuation loses
    the `│` rail, so the box visibly breaks on a narrow window. Wrapping it
    here keeps the rail, and each line carries its OWN escape span rather than
    one span crossing a newline."""

    actual = _render(_excluded_note_result())
    note_lines = [
        ln for ln in actual.splitlines() if "excluded from baseline" in ln or "grow history" in ln
    ]

    assert len(note_lines) > 1, "expected the note to wrap, otherwise this guard proves nothing"
    for line in note_lines:
        assert line.startswith("│   "), f"wrapped note line lost its rail: {line!r}"


def test_excluded_note_absent_when_no_runs_excluded():
    """Default counts (0) -> no note line at all, so the common healthy case
    is byte-identical to before this batch."""
    actual = _render(_normal_result())
    assert "excluded from baseline" not in _unwrapped(actual)


def test_excluded_note_only_names_nonzero_categories():
    result = CompareResult(
        verdicts=_insufficient_data_result().verdicts,
        calibration=CalibrationReport(
            metrics=(), status="insufficient-data", runs_flagged=0, runs_total=0
        ),
        excluded_same_commit=0,
        excluded_no_commit=4,
    )
    flat = _unwrapped(_render(result))
    assert "4 run(s) excluded from baseline" in flat
    assert "4 without a git commit" in flat
    assert "on the current commit" not in flat  # zero category omitted


# ===== (g) restyle: the box, the labelled columns, and WHERE color lands =====


def test_box_header_names_what_was_compared():
    """The `┌─` header is the only place a reader learns which flow, mode and
    device produced the table — a `CompareResult` carries none of the three,
    which is why `render_compare` now requires them."""

    actual = _render(_normal_result())

    assert actual.startswith("┌─ perfvibe compare · demo · warm · Pixel 8 Pro\n")
    assert actual.endswith("└─\n")


def test_box_never_draws_a_right_hand_border():
    for line in _render(_normal_result()).splitlines():
        assert not line.rstrip().endswith("│") or line.rstrip() == "│"


def test_columns_are_labelled_so_latest_and_baseline_are_distinguishable():
    """The old `1310.0 vs 812.0` gave no way to tell which number was which.
    Both values must now sit under a header that names them."""

    lines = _render(_normal_result()).splitlines()
    header = next(line for line in lines if "METRIC" in line)
    row = next(line for line in lines if "checkout" in line)

    assert header.index("LATEST") < header.index("BASELINE")
    assert row.index("102.0 ms") < row.index("100.0 ms")


def test_sanity_label_and_excluded_note_sit_inside_the_box():
    lines = _render(_excluded_note_result()).splitlines()

    assert any(line.startswith("│   ✓") or line.startswith("│   ·") for line in lines)
    assert any(line.startswith("│   note: ") for line in lines)


def test_color_paints_the_glyph_delta_and_status_and_nothing_else():
    """The old renderer reddened the WHOLE row, which made the numbers harder
    to read than leaving them alone. Exactly three cells carry color now, so
    stripping the escapes must leave the row byte-identical to the color-off
    one — and every escape must sit against the glyph, the Δ or the status
    word, never against a number."""

    plain = _render(_regression_result())
    painted = _render(_regression_result(), color=True)

    assert painted.replace(BOLD_RED, "").replace(RESET, "") == plain
    assert painted.count(BOLD_RED) == 3
    assert f"{BOLD_RED}{GLYPH_OFFENDER}{RESET}" in painted
    assert f"{BOLD_RED}↑ +20.0%{RESET}" in painted
    assert f"{BOLD_RED}REGRESSION{RESET}" in painted
    # Three opened spans, three named cells, and stripping them reproduces the
    # plain row exactly — so no fourth cell (no number) can be carrying color.


def test_an_improvement_is_green_not_merely_uncoloured():
    """Before the restyle `compare_pretty` had no green at all, so an
    improvement rendered identically to a flat result."""

    painted = _render(_normal_result(), color=True)

    assert f"{BOLD_GREEN}{GLYPH_OK}{RESET}" in painted
    assert f"{BOLD_GREEN}improvement{RESET}" in painted


def test_a_stable_row_is_dimmed_rather_than_shouted():
    painted = _render(_normal_result(), color=True)

    assert f"{DIM}{GLYPH_NEUTRAL}{RESET}" in painted
    assert f"{DIM}stable{RESET}" in painted


# ===== (h) the min→max scale column, per series edge =====


def _scale_cell(actual: str, metric: str) -> str:
    row = next(line for line in actual.splitlines() if metric in line)
    return row.split()[-1]


def test_scale_column_gives_the_sparkline_a_magnitude():
    """`▁▂▃█` is normalized to its own min/max, so it says nothing about how
    far the metric moved: 100->110 and 812->1310 draw identically. The scale
    column is what makes the trend readable."""

    actual = _render(_normal_result())

    assert _scale_cell(actual, "checkout") == "98→102"
    assert _scale_cell(actual, "fps_avg") == "58→65"


def test_scale_of_a_zero_variance_series_shows_min_equal_to_max():
    # Truthful, and it agrees with the flat sparkline drawn beside it.
    assert _scale_cell(_render(_max_eq_min_result()), "checkout") == "100→100"


def test_scale_of_a_single_point_series_is_the_value_not_a_fabricated_range():
    """One observation is not a range, so `100→100` would invent a span that
    was never measured."""

    actual = _render(_single_point_result())

    assert _scale_cell(actual, "checkout") == "100"
    assert "→" not in _scale_cell(actual, "checkout")


def test_scale_of_a_no_baseline_series_still_reports_the_one_value_it_has():
    # `insufficient-data` means no BASELINE, not no data — the latest run was
    # still measured, so its value is honest to show.
    assert _scale_cell(_render(_insufficient_data_result()), "checkout") == "100"


def test_scale_keeps_a_decimal_when_rounding_would_claim_zero_variance():
    """A real `fps_min` series of 58.0-58.2 printed `58→58`, which reads as a
    flat metric right next to a sparkline that visibly moves."""

    result = CompareResult(
        verdicts=(
            Verdict(
                metric_name="fps_min",
                delta_pct=-0.2,
                threshold_pct=5.0,
                status="stable",
                latest_value=57.9,
                baseline_value=58.0,
                unit="fps",
                sample_n=3,
                baseline_commit_n=4,
                series=(58.2, 58.1, 58.0, 57.9),
                floor=1.0,
            ),
        ),
        calibration=_reasonable_calibration(),
    )

    assert _scale_cell(_render(result), "fps_min") == "57.9→58.2"


def test_scale_of_an_empty_series_is_the_missing_value_marker():
    result = CompareResult(
        verdicts=(
            Verdict(
                metric_name="checkout",
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
            ),
        ),
        calibration=_reasonable_calibration(),
    )

    assert _scale_cell(_render(result), "checkout") == "-"


# ===== (i) the device label in the box header =====


def test_device_label_shows_the_model_half_of_the_device_key():
    assert "· Pixel 8 Pro\n" in _render(_normal_result())


def test_device_label_degrades_to_a_readable_phrase_with_no_device_attached():
    """`run` derives `unknown|unknown|physical` when no device answers, and a
    bare `unknown` reads as nothing at all in a header."""

    actual = render_compare(
        _normal_result(), flow_name="demo", mode="warm", device_key="unknown|unknown|physical"
    )

    assert actual.startswith("┌─ perfvibe compare · demo · warm · unknown device\n")
