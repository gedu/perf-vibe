"""Golden tests for `cli/output/reassure_history_pretty.render_reassure_history`
(SKILL rule 8: "Golden files for pretty output with color forced off"), plus
non-golden guards on the properties this view exists for — a golden proves
the output is STABLE, never that it is right.

INVARIANT I1, IN CHART FORM (design "Renderers", `reassure_history_pretty.py`
row: "two per-series sections... Two per-series sections instead of one
combined table is invariant I1 surfacing visually"): `duration` and `count`
are reassure's two independently-reduced series — different lengths,
different runs — so this view draws TWO separate, self-contained sections,
each with its OWN `chart_lines` chart, `sparkline` window and table. They
never share a y-axis, never interleave rows, and are never drawn as one
chart with two lines.

THE X-LABEL FALLBACK CHAIN (task 3.6): short `commit_hash`, else the date
part of `ordered_at`, else `#<import_id>`. Levels 1 and 2 are reachable
through real store data (`commit_hash`/`created_date` are both nullable
columns, `db/migrations/0005_add_reassure_tables.sql`) and are covered at
the CLI/integration layer in `test_cli_reassure_history.py`. Level 3
(`#<import_id>`) is NOT reachable through real store data — `imported_at`
is always populated at insert time (`store_sqlite.py`'s
`self._clock.now_utc_iso()`), so `ordered_at`
(`COALESCE(created_date, imported_at)`) is never empty on a real row. It IS
exercised here, directly against the renderer, via a hand-built
`ReassureSeriesPoint` with an empty `ordered_at` — this module owns the
label logic and is the correct layer to prove that branch."""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.reassure_history_pretty import render_reassure_history
from perf.domain.model import HistoryMetric, ReassureEntryRow, ReassureSeriesPoint

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


def _metric(**overrides: object) -> HistoryMetric:
    defaults: dict[str, object] = {
        "metric_name": "duration_ms",
        "p50": 10.0,
        "p90": 12.0,
        "n": 5,
        "unit": "ms",
    }
    defaults.update(overrides)
    return HistoryMetric(**defaults)


def _entry(**overrides: object) -> ReassureEntryRow:
    defaults: dict[str, object] = {
        "entry_id": 1,
        "name": _NAME,
        "entry_type": "render",
        "runs": 5,
        "duration": _metric(),
        "count": _metric(metric_name="render_count", p50=1.0, p90=1.0, unit="count"),
        "initial_update_count": None,
    }
    defaults.update(overrides)
    return ReassureEntryRow(**defaults)


def _point(**overrides: object) -> ReassureSeriesPoint:
    defaults: dict[str, object] = {
        "import_id": 1,
        "ordered_at": "2026-01-01",
        "ordering_key": "created_date",
        "entry": _entry(),
        "commit_hash": "abc1234",
        "branch": "main",
    }
    defaults.update(overrides)
    return ReassureSeriesPoint(**defaults)


def _normal_points() -> tuple[ReassureSeriesPoint, ...]:
    durations = [100.0, 105.0, 102.0, 130.0]
    counts = [1.0, 1.0, 2.0, 2.0]
    return tuple(
        _point(
            import_id=1000 + idx,
            ordered_at=f"2026-01-0{idx + 1}",
            commit_hash=f"{'abcdef' + str(idx)}",
            entry=_entry(
                entry_id=1000 + idx,
                duration=_metric(p50=durations[idx], p90=durations[idx] + 8, n=5),
                count=_metric(
                    metric_name="render_count",
                    p50=counts[idx],
                    p90=counts[idx],
                    n=5,
                    unit="count",
                ),
            ),
        )
        for idx in range(4)
    )


def test_normal_series_matches_golden(request):
    actual = render_reassure_history(_NAME, _normal_points(), color=False)
    _assert_or_update_golden(request, "reassure_history_normal.txt", actual)


def test_normal_series_has_no_ansi_escapes():
    actual = render_reassure_history(_NAME, _normal_points(), color=False)
    assert _ANSI_ESCAPE not in actual


def _single_point() -> tuple[ReassureSeriesPoint, ...]:
    return (
        _point(
            import_id=2001,
            ordered_at="2026-02-01",
            commit_hash="deadbee0",
            entry=_entry(
                entry_id=2001,
                duration=_metric(p50=100.0, p90=110.0, n=5),
                count=_metric(metric_name="render_count", p50=1.0, p90=1.0, n=5, unit="count"),
            ),
        ),
    )


def test_single_point_matches_golden(request):
    actual = render_reassure_history(_NAME, _single_point(), color=False)
    _assert_or_update_golden(request, "reassure_history_single_point.txt", actual)


def _empty_series_points() -> tuple[ReassureSeriesPoint, ...]:
    """`count` was never measured for ANY point in this window — `duration`
    still draws a normal section, `count` says so instead of drawing an
    empty axis."""

    return tuple(
        _point(
            import_id=3000 + idx,
            ordered_at=f"2026-03-0{idx + 1}",
            commit_hash=f"cafe00{idx}",
            entry=_entry(
                entry_id=3000 + idx,
                duration=_metric(p50=100.0 + idx, p90=110.0 + idx, n=5),
                count=None,
            ),
        )
        for idx in range(3)
    )


def test_empty_series_matches_golden(request):
    actual = render_reassure_history(_NAME, _empty_series_points(), color=False)
    _assert_or_update_golden(request, "reassure_history_empty_series.txt", actual)


def _zero_variance_points() -> tuple[ReassureSeriesPoint, ...]:
    return tuple(
        _point(
            import_id=4000 + idx,
            ordered_at=f"2026-04-0{idx + 1}",
            commit_hash=f"f1a70{idx}",
            entry=_entry(
                entry_id=4000 + idx,
                duration=_metric(p50=100.0, p90=100.0, n=5),
                count=_metric(metric_name="render_count", p50=1.0, p90=1.0, n=5, unit="count"),
            ),
        )
        for idx in range(3)
    )


def test_zero_variance_matches_golden(request):
    actual = render_reassure_history(_NAME, _zero_variance_points(), color=False)
    _assert_or_update_golden(request, "reassure_history_zero_variance.txt", actual)


def test_zero_variance_renders_without_dividing_by_zero():
    """Same guard `sparkline`/`chart_lines` need in every view that draws a
    series — a `max == min` window must not raise or produce `nan`."""
    actual = render_reassure_history(_NAME, _zero_variance_points(), color=False)
    assert "nan" not in actual.lower()


# ===== I1: the two sections stay unpairable =====


def test_duration_and_count_are_two_separate_sections():
    actual = render_reassure_history(_NAME, _normal_points(), color=False)
    assert "duration" in actual
    assert "count" in actual


def test_sections_never_share_a_single_combined_chart():
    """[unmissable] I1 in chart form: the two series must never be drawn as
    one chart with two lines sharing a y-axis. Each section owns its own
    `┤` axis block; there are exactly two axis blocks with DIFFERENT top
    ticks (100+8=108 for duration's max p90, 2.0 for count's max p90) —
    proof they are not one shared axis."""
    actual = render_reassure_history(_NAME, _normal_points(), color=False)
    assert "138.0 ┤" in actual  # duration's max p90 (130 + 8)
    assert "2.0 ┤" in actual  # count's max p90


def test_a_gap_in_one_series_never_shifts_the_other_series_labels():
    """The two series are independently-lengthed and independently
    charted — a point missing from one series' chart must not shift or
    truncate the OTHER series' labels."""
    actual = render_reassure_history(_NAME, _empty_series_points(), color=False)
    assert "no chart" in actual or "no p90" in actual.lower()
    # duration's own chart is untouched by count's absence
    assert "cafe000" in actual


# ===== the box =====


def test_render_is_wrapped_in_an_open_right_box():
    actual = render_reassure_history(_NAME, _normal_points())

    assert actual.startswith(f"┌─ perfvibe reassure history · {_NAME} · 4 import(s)\n")
    assert actual.rstrip("\n").endswith("└─")
    body = actual.splitlines()[1:-1]
    assert all(line.startswith("│") for line in body), "every body line needs the left rail"
    content = [line for line in body if line.strip() != "│"]
    assert not any(line.rstrip().endswith("│") for line in content), "the box stays open-right"


def test_name_is_visible_in_the_header():
    actual = render_reassure_history(_NAME, _normal_points())
    assert _NAME in actual.splitlines()[0]


# ===== x-label fallback chain =====


def test_x_label_uses_short_commit_hash_when_present():
    points = (_point(commit_hash="abcdef1234567", ordered_at="2026-05-01"),)
    actual = render_reassure_history(_NAME, points)
    assert "abcdef1" in actual
    assert "abcdef1234567" not in actual  # truncated to 7


def test_x_label_falls_back_to_date_part_of_ordered_at_when_commit_absent():
    points = (_point(commit_hash=None, ordered_at="2026-05-02T10:00:00+00:00"),)
    actual = render_reassure_history(_NAME, points)
    assert "2026-05-02" in actual
    assert "10:00:00" not in actual  # date part only, matching `history_pretty._date_part`


def test_x_label_falls_back_to_import_id_when_commit_and_ordered_at_are_both_absent():
    """[unmissable] The third level of the chain — NOT reachable through
    real store data (see module docstring); exercised here directly."""
    points = (_point(import_id=9999, commit_hash=None, ordered_at=""),)
    actual = render_reassure_history(_NAME, points)
    assert "#9999" in actual


def test_mixed_series_falls_back_at_different_levels_per_point():
    """The case where an off-by-one in the fallback shows up as a
    mislabelled axis rather than a crash: three points, each landing on a
    DIFFERENT level of the chain."""
    points = (
        _point(
            import_id=1, commit_hash="feedcafe", ordered_at="2026-06-01", entry=_entry(entry_id=1)
        ),
        _point(import_id=2, commit_hash=None, ordered_at="2026-06-02", entry=_entry(entry_id=2)),
        _point(import_id=3, commit_hash=None, ordered_at="", entry=_entry(entry_id=3)),
    )
    actual = render_reassure_history(_NAME, points)
    assert "feedcaf" in actual
    assert "2026-06-02" in actual
    assert "#3" in actual


def test_x_axis_labels_never_collide_when_a_wide_date_label_is_present():
    """[unmissable] `chart_lines`'s default column is 8 chars wide, and a
    `YYYY-MM-DD` date-fallback label is 10 — at the default width the CLI's
    real output showed the date label running straight into the NEXT
    column's label with no separator at all
    (`2026-01-02ccc3333`). The chart column must widen to fit the widest
    label actually charted, for every point, not just the wide one."""
    points = (
        _point(import_id=1, commit_hash="aaa1111", ordered_at="2026-01-01"),
        _point(import_id=2, commit_hash=None, ordered_at="2026-01-02"),
        _point(import_id=3, commit_hash="ccc3333", ordered_at="2026-01-03"),
    )
    actual = render_reassure_history(_NAME, points)

    label_line = next(line for line in actual.splitlines() if "aaa1111" in line and "└" not in line)
    assert "2026-01-02ccc3333" not in label_line
    assert "2026-01-02" in label_line
    assert "ccc3333" in label_line
    # at least one space separates every label from the next
    for label in ("aaa1111", "2026-01-02", "ccc3333"):
        index = label_line.index(label)
        after = label_line[index + len(label) :]
        assert after == "" or after[0] == " "


# ===== chart/table truncation over a long window =====


def _long_points(n: int) -> tuple[ReassureSeriesPoint, ...]:
    return tuple(
        _point(
            import_id=5000 + idx,
            ordered_at=f"2026-07-{idx + 1:02d}",
            commit_hash=f"{idx:02d}abcde",
            entry=_entry(
                entry_id=5000 + idx,
                duration=_metric(p50=100.0 + idx, p90=110.0 + idx, n=5),
                count=_metric(metric_name="render_count", p50=1.0, p90=1.0, n=5, unit="count"),
            ),
        )
        for idx in range(n)
    )


def test_chart_and_table_truncate_to_the_most_recent_rows_and_the_box_admits_it():
    """Copied section-for-section from `history_pretty`'s own truncation
    guard: the header sparkline still spans the WHOLE window, but the chart
    and table both cap to the most recent points, and the box says so."""
    actual = render_reassure_history(_NAME, _long_points(20), color=False)

    assert "charting the last 8 of 20 imports" in actual
    for idx in range(12):
        assert f"{idx:02d}abcde" not in actual
        assert str(5000 + idx) not in actual
    for idx in range(12, 20):
        assert f"{idx:02d}abcde" in actual
        assert str(5000 + idx) in actual


def test_no_truncation_notice_when_the_whole_window_fits():
    assert "charting the last" not in render_reassure_history(_NAME, _long_points(4), color=False)


# ===== color =====


def test_color_off_emits_no_ansi():
    for points in (
        _normal_points(),
        _single_point(),
        _empty_series_points(),
        _zero_variance_points(),
    ):
        assert _ANSI_ESCAPE not in render_reassure_history(_NAME, points, color=False)
