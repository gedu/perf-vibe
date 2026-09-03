"""Golden tests for `cli/output/reassure_compare_pretty.render_reassure_compare`
(SKILL rule 8: "Golden files for pretty output with color forced off"), plus
non-golden guards on the properties this view exists for — a golden proves
the output is STABLE, never that it is right.

`compare` is the ONE view in the whole `reassure` capability that renders a
verdict at all (`reassure_history_pretty.py`'s own module docstring says so
explicitly). Cases: (a) a normal comparison with one `stable` and one
`regression` verdict, uppercase-only on the offender; (b) an `insufficient-
data` comparison (below `MIN_BASELINE_IMPORTS`) — never rendered as a
silent `stable`; (c) an `improvement` verdict; (d) the D5 sentence present
below the table; (e) the D5 sentence OMITTED entirely (both sides `0`) —
the row the design itself calls "most easily missed", and this view's own
version of the guard `test_reassure_show_pretty_golden.py` already pins for
the wording table itself (this file does not re-test every D5 wording
variant — that is `reassure_show_pretty`'s job, since `d5_sentence` is
shared verbatim, design "no second source of truth").

`ReassureComparison`/`Verdict` are hand-built directly here — this file
never calls `compare_series` (that is `tests/unit/test_reassure_compare.py`
and `tests/integration/test_cli_reassure_compare.py`'s job) — so every
golden case is fully deterministic and decoupled from domain logic,
matching `test_reassure_history_pretty_golden.py`'s own
`ReassureSeriesPoint`-by-hand precedent."""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.primitives import BOLD_GREEN, BOLD_RED, DIM, RESET
from perf.cli.output.reassure_compare_pretty import render_reassure_compare
from perf.domain.model import HistoryMetric, ReassureEntryRow, ReassureSeriesPoint, Verdict
from perf.domain.reassure_compare import (
    SERIES_DURATION,
    SERIES_RENDER_COUNT,
    ReassureComparison,
    UpdateCountChange,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_ANSI_ESCAPE = "\x1b["

_NAME = "WidgetPanel renders correctly"


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


def _verdict(**overrides: object) -> Verdict:
    defaults: dict[str, object] = {
        "metric_name": SERIES_DURATION,
        "delta_pct": 0.0,
        "threshold_pct": 5.0,
        "status": "stable",
        "latest_value": 100.0,
        "baseline_value": 100.0,
        "unit": "ms",
        "sample_n": 5,
        "baseline_commit_n": 5,
        "series": (100.0, 101.0, 99.0, 100.0),
        "floor": 5.0,
        "higher_is_better": False,
    }
    defaults.update(overrides)
    return Verdict(**defaults)


def _latest_point(**overrides: object) -> ReassureSeriesPoint:
    defaults: dict[str, object] = {
        "import_id": 1004,
        "ordered_at": "2026-01-04",
        "ordering_key": "created_date",
        "entry": ReassureEntryRow(
            entry_id=1004,
            name=_NAME,
            entry_type="render",
            runs=5,
            duration=HistoryMetric(metric_name="duration_ms", p50=99.0, p90=100.0, n=5, unit="ms"),
            count=HistoryMetric(metric_name="render_count", p50=2.0, p90=2.0, n=5, unit="count"),
            initial_update_count=1,
        ),
        "commit_hash": "abcdef1",
        "branch": "main",
    }
    defaults.update(overrides)
    return ReassureSeriesPoint(**defaults)


def _comparison(**overrides: object) -> ReassureComparison:
    defaults: dict[str, object] = {
        "name": _NAME,
        "latest": _latest_point(),
        "baseline_import_n": 5,
        "verdicts": (
            _verdict(metric_name=SERIES_DURATION, unit="ms"),
            _verdict(metric_name=SERIES_RENDER_COUNT, unit="count"),
        ),
        "update_count": UpdateCountChange(baseline=0, latest=1, state="introduced"),
    }
    defaults.update(overrides)
    return ReassureComparison(**defaults)


# ===== (a) normal: one stable, one regression =====


def _normal_comparison() -> ReassureComparison:
    return _comparison(
        verdicts=(
            _verdict(metric_name=SERIES_DURATION, unit="ms", status="stable"),
            _verdict(
                metric_name=SERIES_RENDER_COUNT,
                unit="count",
                status="regression",
                delta_pct=800.0,
                latest_value=9.0,
                baseline_value=1.0,
                series=(1.0, 1.0, 1.0, 9.0),
                higher_is_better=False,
            ),
        ),
    )


def test_normal_comparison_matches_golden(request):
    actual = render_reassure_compare(_normal_comparison(), color=False)
    _assert_or_update_golden(request, "reassure_compare_normal.txt", actual)


def test_regression_row_is_uppercase_stable_row_is_lowercase():
    actual = render_reassure_compare(_normal_comparison(), color=False)
    assert "REGRESSION" in actual
    assert "stable" in actual
    assert "STABLE" not in actual


def test_normal_comparison_has_no_ansi_escapes():
    actual = render_reassure_compare(_normal_comparison(), color=False)
    assert _ANSI_ESCAPE not in actual


# ===== (b) insufficient-data: never rendered as stable =====


def _insufficient_data_comparison() -> ReassureComparison:
    return _comparison(
        baseline_import_n=1,
        verdicts=(
            _verdict(
                metric_name=SERIES_DURATION,
                unit="ms",
                status="insufficient-data",
                delta_pct=0.0,
                latest_value=None,
                baseline_value=None,
                series=(),
                sample_n=0,
                baseline_commit_n=1,
            ),
            _verdict(
                metric_name=SERIES_RENDER_COUNT,
                unit="count",
                status="insufficient-data",
                delta_pct=0.0,
                latest_value=None,
                baseline_value=None,
                series=(),
                sample_n=0,
                baseline_commit_n=1,
            ),
        ),
        update_count=UpdateCountChange(baseline=None, latest=None, state="unknown"),
    )


def test_insufficient_data_matches_golden(request):
    actual = render_reassure_compare(_insufficient_data_comparison(), color=False)
    _assert_or_update_golden(request, "reassure_compare_insufficient_data.txt", actual)


def test_insufficient_data_is_never_rendered_as_stable():
    actual = render_reassure_compare(_insufficient_data_comparison(), color=False)
    assert "insufficient-data" in actual
    assert "stable" not in actual.lower()


# ===== (c) improvement =====


def _improvement_comparison() -> ReassureComparison:
    return _comparison(
        verdicts=(
            _verdict(
                metric_name=SERIES_DURATION,
                unit="ms",
                status="improvement",
                delta_pct=-30.0,
                latest_value=70.0,
                baseline_value=100.0,
                series=(100.0, 98.0, 102.0, 70.0),
            ),
            _verdict(metric_name=SERIES_RENDER_COUNT, unit="count", status="stable"),
        ),
    )


def test_improvement_matches_golden(request):
    actual = render_reassure_compare(_improvement_comparison(), color=False)
    _assert_or_update_golden(request, "reassure_compare_improvement.txt", actual)


def test_improvement_shows_a_down_arrow_never_uppercased():
    actual = render_reassure_compare(_improvement_comparison(), color=False)
    assert "improvement" in actual
    assert "IMPROVEMENT" not in actual


# ===== (d)/(e) D5 sentence present vs. omitted entirely =====


def test_d5_sentence_present_below_the_table():
    actual = render_reassure_compare(
        _comparison(update_count=UpdateCountChange(baseline=0, latest=1, state="introduced")),
        color=False,
    )
    assert "✗ extra mount render introduced (0 -> 1)" in actual
    # Below the table, never a row inside it — the table rule spans exactly
    # `_RULE_WIDTH`, so the sentence line must come strictly after it.
    rule_index = actual.index("─" * 10)
    sentence_index = actual.index("extra mount render introduced")
    assert sentence_index > rule_index


def test_d5_sentence_omitted_entirely_when_both_sides_are_zero():
    """[unmissable] The row `reassure_show_pretty`'s own golden suite calls
    "most easily missed": both sides measured `0` prints NO line at all —
    never a fabricated `0 -> 0` sentence."""
    actual = render_reassure_compare(
        _comparison(update_count=UpdateCountChange(baseline=0, latest=0, state="unchanged")),
        color=False,
    )
    assert "mount render" not in actual


# ===== the box =====


def test_render_is_wrapped_in_an_open_right_box():
    actual = render_reassure_compare(_normal_comparison(), color=False)

    assert actual.startswith(f"┌─ perfvibe reassure compare · {_NAME} · baseline 5 import(s)\n")
    assert actual.rstrip("\n").endswith("└─")
    body = actual.splitlines()[1:-1]
    assert all(line.startswith("│") for line in body), "every body line needs the left rail"
    content = [line for line in body if line.strip() != "│"]
    assert not any(line.rstrip().endswith("│") for line in content), "the box stays open-right"


def test_name_and_baseline_count_are_visible_in_the_header():
    actual = render_reassure_compare(_comparison(baseline_import_n=7), color=False)
    header = actual.splitlines()[0]
    assert _NAME in header
    assert "7" in header


def test_verdicts_render_in_fixed_order_duration_then_render_count():
    actual = render_reassure_compare(_normal_comparison(), color=False)
    duration_index = actual.index("duration_ms")
    count_index = actual.index("render_count")
    assert duration_index < count_index


# ===== color =====


def test_color_on_paints_only_glyph_delta_and_status_for_a_regression():
    """Mirrors `compare_pretty`'s own "only three cells are ever painted"
    guarantee: everything else — METRIC, LATEST, BASELINE, TREND — stays
    unstyled even under `color=True`."""
    actual = render_reassure_compare(_normal_comparison(), color=True)
    assert BOLD_RED in actual
    assert RESET in actual


def test_color_on_paints_green_for_an_improvement():
    actual = render_reassure_compare(_improvement_comparison(), color=True)
    assert BOLD_GREEN in actual


def test_color_on_paints_dim_for_insufficient_data():
    actual = render_reassure_compare(_insufficient_data_comparison(), color=True)
    assert DIM in actual


def test_color_off_emits_no_ansi_in_any_state():
    for comparison in (
        _normal_comparison(),
        _insufficient_data_comparison(),
        _improvement_comparison(),
    ):
        assert _ANSI_ESCAPE not in render_reassure_compare(comparison, color=False)
