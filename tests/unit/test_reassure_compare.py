"""Unit tests for `domain/reassure_compare.py` — PURE, no I/O.

This module ships in two waves: PR2b added ONLY the D5 state derivation
(`derive_update_count_change`/`UpdateCountChange`) because `reassure show`
needed it early (design "D5 — State Transition in Both Views";
`tasks.md`'s "D5 GAP — RESOLVED by reordering, not by duplicating a
method"). PR4a (this file's second wave, `tasks.md` 4a.1-4a.9) EXTENDS the
same file with `compare_series`, `ReassureComparison`, `SERIES_DURATION`/
`SERIES_RENDER_COUNT`, `MIN_BASELINE_IMPORTS`, and both I2 guard tests —
the module is never duplicated into a second file.

D5 derivation (design.md:370-372), the exact five states:
  - either side `None` -> `'unknown'` (checked `is None`, NEVER a falsy
    check — the whole point of this module: `0` must never take the
    `None` branch, and `None` must never be silently read as `0`)
  - `0 -> >0` -> `'introduced'`
  - `>0 -> 0` -> `'resolved'`
  - different non-zero values -> `'changed'`
  - else (equal, both non-`None`) -> `'unchanged'` — this function does
    NOT distinguish "both zero" from "both equal non-zero"; that split is
    a PRETTY-VIEW concern (design's table: both-zero omits the line
    entirely, both-non-zero prints one dim sentence) which belongs in
    `cli/output/reassure_show_pretty.py`, not here.

`compare_series` I2 guards (design "I2", `design.md:22-28`): the load-
bearing invariant is that `commit_hash`/`branch` on `ReassureSeriesPoint`
are LABEL-ONLY and `statistics.median_by_commit` never touches reassure
data. Two tests defend this: (1) a behavioral test proving two points
sharing BOTH `commit_hash` AND `branch` (0006's real recorded case) each
still contribute their own value to a PLAIN median, with baseline p90
values chosen so the WRONG (collapsed-by-commit) median and the TRUE
(plain) median genuinely differ — a test where they coincide would pass
under the bug and prove nothing; (2) a static AST test proving the module
never imports or calls `median_by_commit` at all, so the invariant holds
even for inputs no behavioral test happens to construct.
"""

from __future__ import annotations

import ast
from pathlib import Path

from perf.domain import reassure_compare
from perf.domain.model import HistoryMetric, ReassureEntryRow, ReassureSeriesPoint
from perf.domain.reassure_compare import (
    MIN_BASELINE_IMPORTS,
    SERIES_DURATION,
    SERIES_RENDER_COUNT,
    ReassureComparison,
    UpdateCountChange,
    compare_series,
    derive_update_count_change,
)
from perf.domain.regression import STATUS_INSUFFICIENT_DATA


def test_both_none_is_unknown():
    change = derive_update_count_change(None, None)
    assert change.state == "unknown"
    assert change.baseline is None
    assert change.latest is None


def test_baseline_none_latest_measured_is_unknown_not_falsy_compared():
    """[unmissable] `baseline=None, latest=0` MUST be `'unknown'`, never
    `'unchanged'` — a falsy check (`if not baseline`) would treat `None`
    and `0` identically and silently produce the wrong state. This is
    the exact trap `0007_add_reassure_entry_issues.sql` defends against."""
    change = derive_update_count_change(None, 0)
    assert change.state == "unknown"
    assert change.baseline is None


def test_latest_none_baseline_measured_is_unknown():
    change = derive_update_count_change(0, None)
    assert change.state == "unknown"
    assert change.latest is None


def test_zero_to_positive_is_introduced():
    change = derive_update_count_change(0, 1)
    assert change.state == "introduced"
    assert change.baseline == 0
    assert change.latest == 1


def test_positive_to_zero_is_resolved():
    change = derive_update_count_change(1, 0)
    assert change.state == "resolved"


def test_different_nonzero_values_is_changed():
    change = derive_update_count_change(1, 2)
    assert change.state == "changed"


def test_equal_nonzero_values_is_unchanged():
    change = derive_update_count_change(1, 1)
    assert change.state == "unchanged"


def test_equal_zero_values_is_unchanged():
    """The pure function reports `'unchanged'` for both-zero too — the
    PRETTY view (not this function) is what omits the line entirely for
    this specific case (design's table)."""
    change = derive_update_count_change(0, 0)
    assert change.state == "unchanged"


def test_update_count_change_carries_no_delta_field():
    """A13: `UpdateCountChange` is deliberately NOT a `Verdict` and has no
    numeric delta/percentage field — a type that has no delta cannot
    render one."""
    change = derive_update_count_change(1, 2)
    assert not hasattr(change, "delta_pct")
    assert isinstance(change, UpdateCountChange)


# ===== `compare_series` (PR4a, second wave) =====


def _point(
    import_id: int,
    *,
    duration_p90: float | None = None,
    duration_n: int = 5,
    count_p90: float | None = None,
    count_n: int = 5,
    initial_update_count: int | None = None,
    commit_hash: str | None = None,
    branch: str | None = None,
    name: str = "HomeScreen",
) -> ReassureSeriesPoint:
    """Hand-built `ReassureSeriesPoint` — no I/O, no store. `duration`/
    `count` stay `None` when their `*_p90` arg is omitted (matches the
    real read model's "zero stored samples for this entry" convention)."""

    duration = (
        HistoryMetric(metric_name=SERIES_DURATION, p90=duration_p90, n=duration_n, unit="ms")
        if duration_p90 is not None
        else None
    )
    count = (
        HistoryMetric(metric_name=SERIES_RENDER_COUNT, p90=count_p90, n=count_n, unit="count")
        if count_p90 is not None
        else None
    )
    return ReassureSeriesPoint(
        import_id=import_id,
        ordered_at=f"2026-01-{import_id:02d}",
        ordering_key="created_date",
        entry=ReassureEntryRow(
            entry_id=import_id,
            name=name,
            entry_type="render",
            runs=10,
            duration=duration,
            count=count,
            initial_update_count=initial_update_count,
        ),
        commit_hash=commit_hash,
        branch=branch,
    )


def test_compare_series_returns_none_for_empty_points():
    assert compare_series((), threshold_pct=5.0, floors={"ms": 5.0, "count": 0.0}) is None


def test_two_points_sharing_commit_and_branch_each_contribute_to_plain_median():
    """[unmissable — I2 guard 1] Two baseline points share BOTH
    `commit_hash` AND `branch` — 0006's real recorded baseline/current
    pair. If a bug first collapsed same-commit points via
    `statistics.median_by_commit` before taking the outer median, the
    baseline would be 17.5 (median of the per-commit medians `{15.0,
    20.0}`); the correct behavior — a PLAIN `statistics.median` over all
    three per-import p90 values, `[5.0, 20.0, 25.0]` — is 20.0. The two
    numbers are deliberately different so a test unable to tell them apart
    would pass under the bug and prove nothing."""
    baseline = [
        _point(1, duration_p90=5.0, commit_hash="aaa1111", branch="main"),
        _point(2, duration_p90=25.0, commit_hash="aaa1111", branch="main"),  # shares both
        _point(3, duration_p90=20.0, commit_hash="bbb2222", branch="main"),
    ]
    latest = _point(4, duration_p90=20.0, commit_hash="ccc3333", branch="main")

    comparison = compare_series(
        (*baseline, latest), threshold_pct=5.0, floors={"ms": 5.0, "count": 0.0}
    )

    assert comparison is not None
    assert comparison.baseline_import_n == 3

    duration_verdict = comparison.verdicts[0]
    assert duration_verdict.metric_name == SERIES_DURATION
    assert duration_verdict.baseline_value == 20.0  # TRUE plain median, never 17.5


def test_module_never_imports_or_calls_median_by_commit():
    """[unmissable — I2 guard 2] AST-based, deliberately NOT a textual
    substring check. This module's OWN docstring intentionally NAMES
    `median_by_commit` by name — it is exactly the bug the I2 invariant
    defends against, and a warning that names it is useful documentation,
    not a violation to strip. A substring assertion
    (`"median_by_commit" not in source`) would therefore fail on a
    correct, bug-free module the instant that warning exists — backwards,
    since the warning is what stops a future reader from reintroducing the
    bug it names. The precedent this guard follows
    (`tests/unit/test_domain_boundary.py`'s `_imported_module_names`,
    `:17-38`) never does textual matching either — it parses real
    `Import`/`ImportFrom` AST nodes. So this test parses
    `reassure_compare.py` with `ast` and asserts `median_by_commit` is
    never IMPORTED and never CALLED; a comment or docstring naming it
    stays allowed. Do not "simplify" this back to a substring check."""

    source = Path(reassure_compare.__file__).read_text()
    tree = ast.parse(source)

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported_names.update(alias.name for alias in node.names)
    assert "median_by_commit" not in imported_names, (
        "reassure_compare.py must never import median_by_commit (I2)"
    )

    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)
    assert "median_by_commit" not in called_names, (
        "reassure_compare.py must never call median_by_commit (I2)"
    )


def test_fewer_than_min_baseline_imports_is_insufficient_data_never_stable():
    """Below `MIN_BASELINE_IMPORTS` (3), BOTH verdicts must report
    `insufficient-data` — never a silent `stable` that looks like a clean
    pass. Baseline and latest values are IDENTICAL, which a broken guard
    would happily classify `stable`."""
    baseline = [
        _point(1, duration_p90=20.0, count_p90=10.0),
        _point(2, duration_p90=20.0, count_p90=10.0),
    ]
    latest = _point(3, duration_p90=20.0, count_p90=10.0)

    comparison = compare_series(
        (*baseline, latest), threshold_pct=5.0, floors={"ms": 5.0, "count": 0.0}
    )

    assert comparison is not None
    assert comparison.baseline_import_n == 2
    assert comparison.baseline_import_n < MIN_BASELINE_IMPORTS
    for verdict in comparison.verdicts:
        assert verdict.status == STATUS_INSUFFICIENT_DATA


def test_render_count_floor_defaults_to_zero_and_higher_is_better_is_false():
    """D7: the `count` series floor is exactly `0.0`, defaulting safely
    even when the caller's `floors` mapping omits a `count` key entirely
    (matches `adapters/analyzer_sql.py`'s `_effective_floor` fallback:
    `self._floors.get(unit, 0.0)`). A6: `higher_is_better` is the literal
    `False` on BOTH verdicts."""
    baseline = [
        _point(1, duration_p90=20.0, count_p90=10.0),
        _point(2, duration_p90=20.0, count_p90=10.0),
        _point(3, duration_p90=20.0, count_p90=10.0),
    ]
    latest = _point(4, duration_p90=20.0, count_p90=11.0)

    comparison = compare_series(
        (*baseline, latest),
        threshold_pct=5.0,
        floors={"ms": 5.0},  # no "count" key at all
    )

    assert comparison is not None
    render_count_verdict = comparison.verdicts[1]
    assert render_count_verdict.metric_name == SERIES_RENDER_COUNT
    assert render_count_verdict.floor == 0.0
    for verdict in comparison.verdicts:
        assert verdict.higher_is_better is False


def test_verdicts_are_in_fixed_order_duration_then_render_count():
    points = [
        _point(1, duration_p90=20.0, count_p90=10.0),
        _point(2, duration_p90=20.0, count_p90=10.0),
        _point(3, duration_p90=20.0, count_p90=10.0),
        _point(4, duration_p90=20.0, count_p90=10.0),
    ]
    comparison = compare_series(points, threshold_pct=5.0, floors={"ms": 5.0, "count": 0.0})

    assert comparison is not None
    assert [verdict.metric_name for verdict in comparison.verdicts] == [
        SERIES_DURATION,
        SERIES_RENDER_COUNT,
    ]


def test_update_count_change_uses_latest_vs_previous_not_baseline_median():
    """`compare_series` wires `UpdateCountChange` from `points[-2]` (the
    immediately PREVIOUS import) vs `points[-1]` (latest) — never a median
    across the baseline window (design `design.md:212-214`: "a median of
    `int | None` state values is meaningless"). The oldest baseline
    points below carry a DIFFERENT `initial_update_count` than the
    immediate previous point specifically so a median-based (buggy) wiring
    would disagree with this assertion: median([5, 5, 1]) == 5 (`changed`
    vs. latest `1`), but the correct previous-only value is `1`
    (`unchanged` vs. latest `1`)."""
    points = [
        _point(1, duration_p90=20.0, initial_update_count=5),  # oldest — must be ignored
        _point(2, duration_p90=20.0, initial_update_count=5),
        _point(3, duration_p90=20.0, initial_update_count=1),  # immediate previous
        _point(4, duration_p90=20.0, initial_update_count=1),  # latest
    ]

    comparison = compare_series(points, threshold_pct=5.0, floors={"ms": 5.0, "count": 0.0})

    assert comparison is not None
    assert comparison.update_count == UpdateCountChange(baseline=1, latest=1, state="unchanged")


def test_comparison_name_and_latest_come_from_the_last_point():
    points = [
        _point(1, duration_p90=20.0, name="HomeScreen"),
        _point(2, duration_p90=20.0, name="HomeScreen"),
        _point(3, duration_p90=20.0, name="HomeScreen"),
        _point(4, duration_p90=21.0, name="HomeScreen"),
    ]

    comparison = compare_series(points, threshold_pct=5.0, floors={"ms": 5.0, "count": 0.0})

    assert comparison is not None
    assert isinstance(comparison, ReassureComparison)
    assert comparison.name == "HomeScreen"
    assert comparison.latest is points[-1]
    assert comparison.latest.import_id == 4
