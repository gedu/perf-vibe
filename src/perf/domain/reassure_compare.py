"""D5 state-transition derivation for reassure's `issues.initialUpdateCount`
(design "D5 — State Transition in Both Views", `design.md:368-393`).

PURE MODULE — no adapter imports, no I/O (SKILL rule 1). This module is
created EARLY, in PR2b, ahead of the slice that owns the rest of it
(`tasks.md`'s PR4a: `compare_series`, `ReassureComparison`,
`SERIES_DURATION`/`SERIES_RENDER_COUNT`, `MIN_BASELINE_IMPORTS`, and both
`median_by_commit` I2 guard tests). `tasks.md`'s "D5 GAP — RESOLVED by
reordering, not by duplicating a method" explains why: `design.md:126`
places `UpdateCountChange` and its derivation here, in
`domain/reassure_compare.py`, but the design's own slice map assigns that
module to PR4a — a module PR2b's `reassure show` needs part of RIGHT NOW.

Resolution (an explicit sequencing decision, not a silent deviation):
create this file now with ONLY `UpdateCountChange` and
`derive_update_count_change` — the two things `design.md:174-177` and
`:370-372` describe as pure state derivation with zero dependency on
`compare_series`, floors, or `MIN_BASELINE_IMPORTS`. PR4a EXTENDS this
same file (adding the verdict function and its value objects); it never
creates a second file. The alternative — duplicating this derivation
inside `cli/commands/reassure.py` or `cli/output/reassure_show_pretty.py`
— is explicitly rejected: a second copy is exactly how the `None`-vs-`0`
distinction quietly diverges between two copies later (the same class of
bug `python-architecture` rule 1, locality of behavior, exists to prevent:
a bug in "the D5 rule" must be fixable in ONE place).

`UpdateCountChange` deliberately carries NO delta/percentage field and is
NOT a `Verdict` (design A13) — a type that has no delta cannot render one,
so neither a renderer nor a payload builder can turn a state transition
into a fabricated percentage."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["UpdateCountChange", "derive_update_count_change"]


@dataclass(frozen=True)
class UpdateCountChange:
    """D5. A STATE TRANSITION, not a `Verdict` (design A13): deliberately
    carries no numeric delta field. `baseline`/`latest` are each `int |
    None` — `None` means that import's `issues.initialUpdateCount` was
    never measured; `0` means it WAS measured and came back clean. The two
    facts must never collapse into each other (`0007_add_reassure_entry_
    issues.sql` defends this distinction at length; `ReassureEntryRow.
    initial_update_count`'s docstring restates it on the read-model side).
    `state` is one of `'introduced'`, `'resolved'`, `'changed'`,
    `'unchanged'`, `'unknown'`."""

    baseline: int | None
    latest: int | None
    state: str


def derive_update_count_change(baseline: int | None, latest: int | None) -> UpdateCountChange:
    """The D5 state derivation (design.md:370-372), reused verbatim by
    `reassure show` (PR2b, this slice's first consumer) and later by
    `reassure compare`'s `compare_series` (PR4a) — the None-vs-0 rule
    lives in exactly this one place.

    - either side `None` -> `'unknown'`. Checked with `is None`, NEVER a
      falsy check (`if not baseline`) — a falsy check would treat `0` and
      `None` identically and silently misclassify a real, measured `0` as
      "never measured".
    - `0 -> >0` -> `'introduced'`
    - `>0 -> 0` -> `'resolved'`
    - different non-zero values -> `'changed'`
    - else (equal, both non-`None`) -> `'unchanged'`. This covers BOTH
      "both zero" and "both equal non-zero" — deciding whether the
      both-zero case renders a line at all (design's table: it does not)
      is the PRETTY view's job, not this pure function's."""

    if baseline is None or latest is None:
        return UpdateCountChange(baseline=baseline, latest=latest, state="unknown")
    if baseline == 0 and latest > 0:
        return UpdateCountChange(baseline=baseline, latest=latest, state="introduced")
    if baseline > 0 and latest == 0:
        return UpdateCountChange(baseline=baseline, latest=latest, state="resolved")
    if baseline != latest:
        return UpdateCountChange(baseline=baseline, latest=latest, state="changed")
    return UpdateCountChange(baseline=baseline, latest=latest, state="unchanged")
