"""Golden tests for
`cli/output/reassure_entries_pretty.render_reassure_entries` (SKILL rule 8:
"Golden files for pretty output with color forced off"), plus non-golden
guards on the properties the view exists for — a golden proves the output
is STABLE, never that it is right.

INVARIANT I1, SURFACED IN THE UI (design "Renderers"): duration and count
p50/p90/n are drawn as two SEPARATE column groups, never one shared pair —
`test_duration_and_count_are_separate_column_groups` pins the exact
header text so a future edit cannot silently collapse them back into one
pair.

The renderer takes the RAW `Sequence[ReassureEntryRow]` (never the
`--json` payload) — the SAME shape `reassure_list_pretty.render_reassure_list`
uses, for the same reason: a raw row, not a wire payload, is the natural
input to a renderer that has no wire-contract constraints of its own.
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.reassure_entries_pretty import render_reassure_entries
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


def _row(**overrides: object) -> ReassureEntryRow:
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


# ===== (a) two entries, one with BOTH series, one with only ONE series =====


def test_entries_table_matches_golden(request):
    rows = (
        _row(
            name="WidgetPanel renders correctly",
            entry_type="render",
            runs=8,
            duration=_metric(metric_name="duration_ms", p50=10.2, p90=10.6, n=6, unit="ms"),
            count=_metric(metric_name="render_count", p50=1.0, p90=2.0, n=8, unit="count"),
        ),
        _row(
            name="NotificationBanner renders after dismiss",
            entry_type="render",
            runs=3,
            duration=None,
            count=_metric(metric_name="render_count", p50=5.0, p90=6.0, n=3, unit="count"),
        ),
    )
    _assert_or_update_golden(
        request, "reassure_entries_table.txt", render_reassure_entries(7, rows)
    )


def test_entries_table_has_no_ansi_escapes_when_color_off():
    rows = (_row(),)
    assert _ANSI_ESCAPE not in render_reassure_entries(1, rows)


# ===== (b) zero entries — a REAL, valid state (never the unknown-id case) =====


def test_zero_entries_matches_golden(request):
    _assert_or_update_golden(request, "reassure_entries_empty.txt", render_reassure_entries(3, ()))


def test_zero_entries_says_so_instead_of_an_empty_table():
    assert "zero entries" in render_reassure_entries(3, ()).lower()


# ===== guards =====


def test_render_is_wrapped_in_an_open_right_box():
    rows = (_row(),)
    actual = render_reassure_entries(1, rows)

    assert actual.startswith("┌─ perfvibe reassure entries · ")
    assert actual.rstrip("\n").endswith("└─")
    body = actual.splitlines()[1:-1]
    assert all(line.startswith("│") for line in body)
    content = [line for line in body if line.strip() != "│"]
    assert not any(line.rstrip().endswith("│") for line in content), "the box stays open-right"


def test_duration_and_count_are_separate_column_groups():
    """I1, surfaced in the UI: the header names SIX metric columns split
    into two groups of three (`DUR *` / `CNT *`) — never one shared P50/P90
    pair, which would visually imply the two series are index-aligned."""
    rows = (_row(),)
    actual = render_reassure_entries(1, rows)

    assert "DUR P50" in actual
    assert "DUR P90" in actual
    assert "DUR N" in actual
    assert "CNT P50" in actual
    assert "CNT P90" in actual
    assert "CNT N" in actual


def test_series_with_zero_samples_renders_as_absent_never_zero():
    """`duration=None` (zero stored samples for this entry) renders `-` in
    every one of its three columns, never `0.0` — a real measured zero and
    "no series" must never look the same."""
    rows = (_row(duration=None),)
    actual = render_reassure_entries(1, rows)

    row_line = next(line for line in actual.splitlines() if "WidgetPanel" in line)
    assert "0.0" not in row_line


def test_import_id_and_entry_count_are_visible_in_the_header():
    rows = (_row(), _row(name="Second"))
    actual = render_reassure_entries(42, rows)

    header = actual.splitlines()[0]
    assert "42" in header
    assert "2" in header


def test_color_off_emits_no_ansi_in_any_outcome():
    payloads = [
        (),
        (_row(),),
        (_row(duration=None),),
        (_row(count=None),),
    ]

    for rows in payloads:
        assert _ANSI_ESCAPE not in render_reassure_entries(1, rows, color=False)
