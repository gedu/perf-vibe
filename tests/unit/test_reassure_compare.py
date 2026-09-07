"""Unit tests for `domain/reassure_compare.py` — PURE, no I/O.

This slice (PR2b) creates the module EARLY and ships ONLY the D5 state
derivation (`derive_update_count_change`) plus its `UpdateCountChange`
value object (design "D5 — State Transition in Both Views";
`tasks.md`'s "D5 GAP — RESOLVED by reordering, not by duplicating a
method"). `reassure show` (this slice) is the FIRST consumer.

`compare_series`, `ReassureComparison`, `SERIES_DURATION`/
`SERIES_RENDER_COUNT`, `MIN_BASELINE_IMPORTS`, and both `median_by_commit`
I2 guard tests land in PR4a (`tasks.md` 4a.1-4a.9) — this file is EXTENDED
there, not replaced; the module is not duplicated into a second file.

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
"""

from __future__ import annotations

from perf.domain.reassure_compare import UpdateCountChange, derive_update_count_change


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
