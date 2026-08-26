"""Golden tests for `cli/output/history_pretty.render_history` (SKILL rule
8: "Golden files for pretty output with color forced off"). Mirrors
`test_compare_pretty_golden.py`'s `--update-golden` pattern. Cases: (a) a
normal multi-metric multi-run series, (b) a single filtered metric, (c) a
series with a missing metric / null summary (sparkline + table edges).
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.history_pretty import render_history
from perf.cli.output.primitives import BOLD_GREEN, BOLD_RED, RESET
from perf.domain.model import HistoryMetric, HistoryRun

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_ANSI_ESCAPE = "\x1b["

_FLOW = "checkout"
_DEVICE = "TestDevice|14|physical"
_MODE = "warm"


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


def _normal_series() -> tuple[HistoryRun, ...]:
    checkout = [100.0, 105.0, 102.0, 130.0]
    fps = [60.0, 61.0, 59.0, 48.0]
    return tuple(
        HistoryRun(
            run_id=1000 + idx,
            started_at=f"2020-01-0{idx + 1}T00:00:0{idx}+00:00",
            git_commit=f"{'abcdef' + str(idx)}",
            source="local:test",
            metrics=(
                HistoryMetric(
                    metric_name="checkout", p50=checkout[idx], p90=checkout[idx] + 8, n=3, unit="ms"
                ),
                HistoryMetric(
                    metric_name="fps_avg", p50=fps[idx], p90=fps[idx] - 2, n=3, unit="fps"
                ),
            ),
        )
        for idx in range(4)
    )


def test_normal_series_matches_golden(request):
    actual = render_history(_FLOW, _DEVICE, _MODE, _normal_series(), color=False)
    _assert_or_update_golden(request, "history_normal.txt", actual)


def test_normal_series_has_no_ansi_escapes():
    actual = render_history(_FLOW, _DEVICE, _MODE, _normal_series(), color=False)
    assert _ANSI_ESCAPE not in actual


def _single_metric_series() -> tuple[HistoryRun, ...]:
    return tuple(
        HistoryRun(
            run_id=2000 + idx,
            started_at=f"2020-02-0{idx + 1}T00:00:00+00:00",
            git_commit=f"deadbee{idx}",
            source="ci",
            metrics=(
                HistoryMetric(
                    metric_name="checkout", p50=100.0 + idx, p90=110.0 + idx, n=5, unit="ms"
                ),
            ),
        )
        for idx in range(3)
    )


def test_single_metric_series_matches_golden(request):
    actual = render_history(_FLOW, _DEVICE, _MODE, _single_metric_series(), color=False)
    _assert_or_update_golden(request, "history_single_metric.txt", actual)


def _gappy_series() -> tuple[HistoryRun, ...]:
    return (
        HistoryRun(
            run_id=3001,
            started_at="2020-03-01T00:00:00+00:00",
            git_commit=None,  # no resolvable commit
            source="local:test",
            metrics=(HistoryMetric(metric_name="checkout", p50=None, p90=None, n=0, unit="ms"),),
        ),
        HistoryRun(
            run_id=3002,
            started_at="2020-03-02T00:00:00+00:00",
            git_commit="cafe123",
            source="local:test",
            metrics=(HistoryMetric(metric_name="checkout", p50=100.0, p90=120.0, n=3, unit="ms"),),
        ),
    )


def test_gappy_series_matches_golden(request):
    actual = render_history(_FLOW, _DEVICE, _MODE, _gappy_series(), color=False)
    _assert_or_update_golden(request, "history_gappy.txt", actual)


def test_gappy_series_renders_dash_for_null_and_no_commit():
    actual = render_history(_FLOW, _DEVICE, _MODE, _gappy_series(), color=False)
    assert "3001" in actual
    assert " - " in actual  # a null p50/p90 or missing-commit dash
    assert _ANSI_ESCAPE not in actual


# ===== (d)-(g) guards on the restyle =====
# Deliberately NOT golden assertions: a golden proves the output is stable,
# never that it is right, so each property the restyle exists for gets its own
# named reason to fail.


def _long_series(n: int) -> tuple[HistoryRun, ...]:
    """`n` runs with a strictly rising p90 — long enough to exercise the
    chart/table truncation.

    The shas are DISTINCT IN THEIR FIRST 7 CHARACTERS on purpose. A first cut
    used `c0ffee{idx:02d}`, and every run past the tenth then rendered as the
    same `c0ffee1`: the table looked plausible and the chart's x axis said
    nothing about which run was which. Real shas do not collide at 7 in a
    20-run window, but a fixture that does makes this test unable to tell a
    dropped run from a truncated one."""

    return tuple(
        HistoryRun(
            run_id=4000 + idx,
            started_at=f"2020-04-{idx + 1:02d}T00:00:00+00:00",
            git_commit=f"{idx:02d}abcde",
            source="ci",
            metrics=(
                HistoryMetric(
                    metric_name="checkout", p50=100.0 + idx, p90=110.0 + idx, n=5, unit="ms"
                ),
            ),
        )
        for idx in range(n)
    )


# ----- (d) the box -----


def test_render_is_wrapped_in_an_open_right_box():
    actual = render_history(_FLOW, _DEVICE, _MODE, _normal_series())

    assert actual.startswith("┌─ perfvibe history · checkout · warm · TestDevice · 4 run(s)\n")
    assert actual.rstrip("\n").endswith("└─")
    body = actual.splitlines()[1:-1]
    assert all(line.startswith("│") for line in body), "every body line needs the left rail"
    # Only lines with CONTENT can close the box; a bare `│` spacer line
    # trivially both starts and ends with the rail.
    content = [line for line in body if line.strip() != "│"]
    assert not any(line.rstrip().endswith("│") for line in content), "the box stays open-right"


def test_device_key_degrades_to_a_readable_phrase_in_the_header():
    actual = render_history(_FLOW, "unknown|unknown|physical", _MODE, _normal_series())

    assert actual.startswith("┌─ perfvibe history · checkout · warm · unknown device · ")


# ----- (e) the chart carries the scale a sparkline cannot -----


def test_chart_labels_the_series_min_and_max_on_the_y_axis():
    """The point of the chart. `checkout` p90 runs 108->138 and a sparkline
    shows NEITHER endpoint, so both have to appear as axis ticks."""

    actual = render_history(_FLOW, _DEVICE, _MODE, _normal_series())

    assert "138.0 ┤" in actual  # max
    assert "108.0 ┤" in actual  # min


def test_a_run_without_a_p90_gets_no_bar_and_no_label():
    """A missing measurement is not a zero. Charting it as one would invent a
    cliff, and it would also shift every label off its own bar."""

    actual = render_history(_FLOW, _DEVICE, _MODE, _gappy_series())
    chart = [line for line in actual.splitlines() if "┤" in line or "└─" in line]

    assert any("120.0 ┤" in line for line in chart)
    assert "r3001" not in actual  # the p90-less run is absent from the x axis
    assert "3001" in actual  # ...but still present as a table row


def test_a_metric_with_no_p90_at_all_says_so_instead_of_drawing_an_empty_axis():
    runs = (
        HistoryRun(
            run_id=5000,
            started_at="2020-05-01T00:00:00+00:00",
            git_commit="beef123",
            source="ci",
            metrics=(HistoryMetric(metric_name="checkout", p50=None, p90=None, n=0, unit="ms"),),
        ),
    )

    actual = render_history(_FLOW, _DEVICE, _MODE, runs)

    assert "no p90 recorded" in actual
    assert "┤" not in actual


# ----- (f) the chart and the table describe the SAME runs -----


def test_chart_and_table_show_the_same_runs_and_the_box_admits_the_truncation():
    """They used to disagree in silence: the sparkline spanned the whole window
    while the table showed the last 8 rows, so lining a bar up with a row lined
    up two different series."""

    actual = render_history(_FLOW, _DEVICE, _MODE, _long_series(20))

    assert "charting the last 8 of 20 runs" in actual
    # The 12 dropped runs appear in NEITHER the chart labels nor the table.
    for idx in range(12):
        assert f"{idx:02d}abcde" not in actual
        assert str(4000 + idx) not in actual
    for idx in range(12, 20):
        assert f"{idx:02d}abcde" in actual
        assert str(4000 + idx) in actual


def test_no_truncation_notice_when_the_whole_window_fits():
    assert "charting the last" not in render_history(_FLOW, _DEVICE, _MODE, _long_series(4))


def test_the_header_sparkline_still_spans_the_whole_window():
    """The chart is capped, so the sparkline is the only thing left that shows
    the entire queried window — it must NOT be capped with it."""

    from perf.cli.output.primitives import sparkline

    runs = _long_series(20)
    expected = sparkline([110.0 + idx for idx in range(20)])

    assert f"window {expected}" in render_history(_FLOW, _DEVICE, _MODE, runs)


# ----- (g) the per-run delta -----


def test_delta_is_a_dash_for_the_first_run_and_a_percentage_after_it():
    actual = render_history(_FLOW, _DEVICE, _MODE, _normal_series())
    checkout_rows = [line for line in actual.splitlines() if "abcdef" in line and "20" in line]

    assert checkout_rows[0].rstrip().endswith("-")  # nothing to compare against
    assert "↑ +4.6%" in actual  # 108.0 -> 113.0
    assert "↓ -2.7%" in actual  # 113.0 -> 110.0
    assert "↑ +25.5%" in actual  # 110.0 -> 138.0


def test_delta_color_is_direction_aware_per_metric():
    """A rising number is bad for a duration and good for a frame rate. Same
    arrow, opposite color — which is why the color cannot be keyed off the sign
    alone."""

    actual = render_history(_FLOW, _DEVICE, _MODE, _normal_series(), color=True)

    assert f"{BOLD_RED}↑ +25.5%{RESET}" in actual  # checkout p90 rose: worse
    assert f"{BOLD_GREEN}↑ +1.7%{RESET}" in actual  # fps_avg p90 rose: better
    assert f"{BOLD_RED}↓ -19.3%{RESET}" in actual  # fps_avg p90 fell: worse


def test_color_off_keeps_every_delta_fact_and_emits_no_ansi():
    """The arrow and the percentage are FACTS; only the good/bad reading is
    color. So `--no-color` may lose the hint and must lose nothing else."""

    actual = render_history(_FLOW, _DEVICE, _MODE, _normal_series(), color=False)

    assert _ANSI_ESCAPE not in actual
    for fact in ("↑ +25.5%", "↑ +1.7%", "↓ -19.3%"):
        assert fact in actual


def test_delta_never_divides_by_a_zero_previous_value():
    """A percentage change from zero is undefined; `+inf%` or a bare `+100%`
    would both be made-up numbers."""

    runs = tuple(
        HistoryRun(
            run_id=6000 + idx,
            started_at=f"2020-06-0{idx + 1}T00:00:00+00:00",
            git_commit=f"zero{idx}",
            source="ci",
            metrics=(HistoryMetric(metric_name="checkout", p50=0.0, p90=p90, n=1, unit="ms"),),
        )
        for idx, p90 in enumerate((0.0, 50.0))
    )

    actual = render_history(_FLOW, _DEVICE, _MODE, runs)

    assert "inf" not in actual
    assert "%" not in actual  # both rows are a bare dash


def test_delta_compares_against_the_last_run_that_had_a_value():
    """Run 2 has no p90, so run 3's delta is against run 1 — comparing against
    a gap is impossible, and skipping the delta entirely would hide a real
    move."""

    p90s = (100.0, None, 150.0)
    runs = tuple(
        HistoryRun(
            run_id=7000 + idx,
            started_at=f"2020-07-0{idx + 1}T00:00:00+00:00",
            git_commit=f"gap{idx}",
            source="ci",
            metrics=(HistoryMetric(metric_name="checkout", p50=None, p90=p90, n=1, unit="ms"),),
        )
        for idx, p90 in enumerate(p90s)
    )

    actual = render_history(_FLOW, _DEVICE, _MODE, runs)

    assert "↑ +50.0%" in actual
