"""Golden tests for `cli/output/history_pretty.render_history` (SKILL rule
8: "Golden files for pretty output with color forced off"). Mirrors
`test_compare_pretty_golden.py`'s `--update-golden` pattern. Cases: (a) a
normal multi-metric multi-run series, (b) a single filtered metric, (c) a
series with a missing metric / null summary (sparkline + table edges).
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.history_pretty import render_history
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
