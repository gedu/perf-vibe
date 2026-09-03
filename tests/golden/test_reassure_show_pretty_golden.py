"""Golden tests for `cli/output/reassure_show_pretty.render_reassure_show`
(SKILL rule 8: "Golden files for pretty output with color forced off"),
plus non-golden guards on the properties the view exists for — a golden
proves the output is STABLE, never that it is right.

D5, THE SIX ROWS FOR FIVE STATES (design "D5 — State Transition in Both
Views", `design.md:376-383`, used VERBATIM — the design's own note: "six
rows for five states ... unchanged splits, and the both-zero case emits
no line at all. That is the row most easily missed"):
  - `introduced` -> one line, non-dim
  - `resolved` -> one line, non-dim
  - `changed` -> one line, non-dim
  - `unchanged` + both `0` -> NO line at all
  - `unchanged` + both non-zero -> one dim line
  - `unknown` -> one dim line

Each state gets its own golden fixture so a future accidental rephrasing
of any ONE line cannot hide behind the others still matching.
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.reassure_show_pretty import render_reassure_show
from perf.domain.model import HistoryMetric, ReassureEntryRow

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_ANSI_ESCAPE = "\x1b["


def _metric(**overrides: object) -> HistoryMetric:
    defaults: dict[str, object] = {
        "metric_name": "duration_ms",
        "p50": 10.2,
        "p90": 10.6,
        "n": 6,
        "unit": "ms",
    }
    defaults.update(overrides)
    return HistoryMetric(**defaults)


def _entry(**overrides: object) -> ReassureEntryRow:
    defaults: dict[str, object] = {
        "entry_id": 1,
        "name": "WidgetPanel renders correctly",
        "entry_type": "render",
        "runs": 8,
        "duration": _metric(metric_name="duration_ms", p50=10.2, p90=10.6, n=6, unit="ms"),
        "count": _metric(metric_name="render_count", p50=1.0, p90=2.0, n=8, unit="count"),
        "initial_update_count": None,
    }
    defaults.update(overrides)
    return ReassureEntryRow(**defaults)


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


# ===== (a) introduced: 0 -> 1 =====


def test_introduced_matches_golden(request):
    entry = _entry(initial_update_count=1)
    _assert_or_update_golden(
        request, "reassure_show_introduced.txt", render_reassure_show(7, entry, 0)
    )


def test_introduced_line_is_the_exact_design_wording():
    entry = _entry(initial_update_count=1)
    actual = render_reassure_show(7, entry, 0)
    assert "✗ extra mount render introduced (0 -> 1)" in actual


# ===== (b) resolved: 1 -> 0 =====


def test_resolved_matches_golden(request):
    entry = _entry(initial_update_count=0)
    _assert_or_update_golden(
        request, "reassure_show_resolved.txt", render_reassure_show(7, entry, 1)
    )


def test_resolved_line_is_the_exact_design_wording():
    entry = _entry(initial_update_count=0)
    actual = render_reassure_show(7, entry, 1)
    assert "✓ extra mount render resolved (1 -> 0)" in actual


# ===== (c) changed: 1 -> 2 =====


def test_changed_matches_golden(request):
    entry = _entry(initial_update_count=2)
    _assert_or_update_golden(
        request, "reassure_show_changed.txt", render_reassure_show(7, entry, 1)
    )


def test_changed_line_is_the_exact_design_wording():
    entry = _entry(initial_update_count=2)
    actual = render_reassure_show(7, entry, 1)
    assert "✗ mount render count changed (1 -> 2)" in actual


# ===== (d) unchanged, BOTH ZERO -> omitted entirely (the easily-missed row) =====


def test_unchanged_both_zero_matches_golden(request):
    entry = _entry(initial_update_count=0)
    _assert_or_update_golden(
        request, "reassure_show_unchanged_zero.txt", render_reassure_show(7, entry, 0)
    )


def test_unchanged_both_zero_emits_no_d5_line_at_all():
    """[unmissable] The row the design itself flags as "most easily
    missed": both sides measured `0` (clean, twice) prints NO line at
    all — never a fabricated `0 -> 0` sentence."""
    entry = _entry(initial_update_count=0)
    actual = render_reassure_show(7, entry, 0)
    assert "mount render" not in actual


# ===== (e) unchanged, both non-zero -> one dim line =====


def test_unchanged_nonzero_matches_golden(request):
    entry = _entry(initial_update_count=1)
    _assert_or_update_golden(
        request, "reassure_show_unchanged_nonzero.txt", render_reassure_show(7, entry, 1)
    )


def test_unchanged_nonzero_line_is_the_exact_design_wording():
    entry = _entry(initial_update_count=1)
    actual = render_reassure_show(7, entry, 1)
    assert "· mount render count unchanged (1)" in actual


# ===== (f) unknown: either side None =====


def test_unknown_matches_golden(request):
    entry = _entry(initial_update_count=0)
    _assert_or_update_golden(
        request, "reassure_show_unknown.txt", render_reassure_show(7, entry, None)
    )


def test_unknown_line_is_the_exact_design_wording():
    entry = _entry(initial_update_count=0)
    actual = render_reassure_show(7, entry, None)
    assert (
        "· mount render diagnostics unavailable (not measured in one of the two imports)" in actual
    )


def test_unknown_never_conflated_with_unchanged_zero():
    """The trap this whole slice guards against: `baseline=None,
    latest=0` renders the UNKNOWN line, never the (omitted) unchanged-zero
    line and never `0 -> 0`."""
    entry = _entry(initial_update_count=0)
    actual = render_reassure_show(7, entry, None)
    assert "diagnostics unavailable" in actual
    assert "0 -> 0" not in actual


# ===== declared-vs-actual runs mismatch line =====


def test_runs_mismatch_line_appears_on_disagreement():
    entry = _entry(
        runs=8,
        duration=_metric(metric_name="duration_ms", n=6, unit="ms"),
        count=_metric(metric_name="render_count", n=8, unit="count"),
    )
    actual = render_reassure_show(7, entry, None)
    assert "declared runs 8 != stored n 6 (duration)" in actual
    assert "declared runs 8 != stored n 8 (count)" not in actual


def test_no_runs_mismatch_line_when_declared_matches_actual():
    entry = _entry(
        runs=6,
        duration=_metric(metric_name="duration_ms", n=6, unit="ms"),
        count=_metric(metric_name="render_count", n=6, unit="count"),
    )
    actual = render_reassure_show(7, entry, None)
    assert "!=" not in actual


# ===== guards =====


def test_render_is_wrapped_in_an_open_right_box():
    entry = _entry(initial_update_count=1)
    actual = render_reassure_show(7, entry, 0)

    assert actual.startswith("┌─ perfvibe reassure show · ")
    assert actual.rstrip("\n").endswith("└─")
    body = actual.splitlines()[1:-1]
    assert all(line.startswith("│") for line in body)
    content = [line for line in body if line.strip() != "│"]
    assert not any(line.rstrip().endswith("│") for line in content), "the box stays open-right"


def test_name_and_import_id_are_visible_in_the_header():
    entry = _entry(name="WidgetPanel renders correctly")
    actual = render_reassure_show(42, entry, None)

    header = actual.splitlines()[0]
    assert "WidgetPanel renders correctly" in header
    assert "42" in header


def test_duration_and_count_are_separate_row_groups():
    """I1, surfaced in the UI: duration and count are two INDEPENDENT
    three-row groups (p50/p90/n), never one shared pair."""
    entry = _entry()
    actual = render_reassure_show(1, entry, None)

    assert "duration p50" in actual
    assert "duration p90" in actual
    assert "duration n" in actual
    assert "count p50" in actual
    assert "count p90" in actual
    assert "count n" in actual


def test_series_with_zero_samples_renders_as_absent_never_zero():
    entry = _entry(duration=None)
    actual = render_reassure_show(1, entry, None)

    row_line = next(line for line in actual.splitlines() if "duration p50" in line)
    assert "0.0" not in row_line


def test_color_off_emits_no_ansi_in_any_state():
    entries_and_baselines = [
        (_entry(initial_update_count=1), 0),  # introduced
        (_entry(initial_update_count=0), 1),  # resolved
        (_entry(initial_update_count=2), 1),  # changed
        (_entry(initial_update_count=0), 0),  # unchanged, both zero
        (_entry(initial_update_count=1), 1),  # unchanged, both non-zero
        (_entry(initial_update_count=0), None),  # unknown
    ]

    for entry, baseline in entries_and_baselines:
        assert _ANSI_ESCAPE not in render_reassure_show(1, entry, baseline, color=False)
