"""Pure comparison logic for reassure series (design "The Verdict
Function", `design.md:161-214`) plus the D5 state-transition derivation
for `issues.initialUpdateCount` (design "D5 — State Transition in Both
Views", `design.md:368-393`).

PURE MODULE — no adapter imports, no I/O (SKILL rule 1).

This module was created EARLY, in PR2b, with ONLY `UpdateCountChange` and
`derive_update_count_change` — `reassure show`'s D5 needed them ahead of
this slice (PR4a), which now EXTENDS the same file rather than creating a
second one (`tasks.md`'s "D5 GAP — RESOLVED by reordering, not by
duplicating a method"; the alternative of duplicating the D5 derivation
inside a CLI/renderer module is explicitly rejected: a second copy is
exactly how the `None`-vs-`0` distinction quietly diverges between two
copies, the class of bug `python-architecture` rule 1 — locality of
behavior — exists to prevent: a bug in "the D5 rule" must be fixable in
ONE place).

`UpdateCountChange` deliberately carries NO delta/percentage field and is
NOT a `Verdict` (design A13) — a type that has no delta cannot render one,
so neither a renderer nor a payload builder can turn a state transition
into a fabricated percentage.

`compare_series` is the ONE public verdict function (design A1 caps this
module at exactly one). I2 (`design.md:22-28`) is the load-bearing
invariant this slice's two RED guard tests defend: `commit_hash`/`branch`
are LABEL-ONLY fields on `ReassureSeriesPoint` — `statistics.median_by_
commit` (`statistics.py:83`, deliberately never imported or called below)
takes exactly one input shape, `Iterable[tuple[str, float]]`, and no
function in this module ever constructs that shape. Two points may share
both `commit_hash` and `branch` (`0006_add_reassure_import_kind.sql`
records a real baseline/current pair that does) and must still each
contribute their own value to a PLAIN `statistics.median` over per-import
p90s — never collapsed by commit first."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from perf.domain import regression, statistics
from perf.domain.model import HistoryMetric, ReassureSeriesPoint, Verdict

__all__ = [
    "MIN_BASELINE_IMPORTS",
    "SERIES_DURATION",
    "SERIES_RENDER_COUNT",
    "ReassureComparison",
    "UpdateCountChange",
    "compare_series",
    "derive_update_count_change",
]

# design "The Verdict Function": the two series names/units `compare_series`
# always produces verdicts for, in this fixed order.
SERIES_DURATION = "duration_ms"  # unit 'ms'
SERIES_RENDER_COUNT = "render_count"  # unit 'count' -> floor 0.0 via D7

# A7: a plain module constant, deliberately NOT `config.min_baseline_commits`
# — that config key's NAME binds it to commit semantics, and borrowing it
# would let a user retuning the flow-world GATE silently retune this
# show-only report too, crossing the D3 boundary.
MIN_BASELINE_IMPORTS = 3


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


@dataclass(frozen=True)
class ReassureComparison:
    """One name's latest-vs-baseline-window comparison (design "The Verdict
    Function"). `verdicts` is FIXED order — `duration_ms` then
    `render_count` — never re-sorted, so a renderer or payload builder may
    index by position. `baseline_import_n` counts IMPORTS in the baseline
    window, never commits (see `compare_series`'s docstring for the
    `classify()` naming friction this field exists to carry outward
    honestly)."""

    name: str
    latest: ReassureSeriesPoint
    baseline_import_n: int
    verdicts: Sequence[Verdict]
    update_count: UpdateCountChange


def compare_series(
    points: Sequence[ReassureSeriesPoint],
    *,
    threshold_pct: float,
    floors: Mapping[str, float],
) -> ReassureComparison | None:
    """The single public verdict function (design "The Verdict Function",
    `design.md:161-214`). `points` is OLDEST->NEWEST, one point per IMPORT
    (D2) — never per commit. `points[-1]` is the latest; `points[:-1]` is
    the baseline window. Returns `None` only when `points` is empty (no
    data at all for this name).

    Per series (`duration_ms`, `render_count`), the baseline value is a
    PLAIN `statistics.median` over the baseline window's non-`None` p90
    values — no grouping, filtering, or joining on `commit_hash`/`branch`
    (I2: `statistics.median_by_commit` is never imported or called here;
    two points sharing both fields — 0006's real recorded case — still
    each contribute their own value to this plain median).

    Fewer than `MIN_BASELINE_IMPORTS` baseline points never falls through
    to `stable`: `regression.classify`'s own `baseline_commit_n < min_n`
    guard reports `insufficient-data` instead, since `baseline_import_n`
    (the count of points in the window, regardless of how many of them
    have a non-`None` p90 for a given series) is what is passed as
    `baseline_commit_n` below — reusing that existing guard instead of a
    second, duplicate threshold check here.

    **Naming friction, documented not renamed**: `classify()`'s parameter
    is `baseline_commit_n` (`regression.py:32`), named for the flow
    world's commit-keyed baseline. Reassure passes a count of baseline
    IMPORTS instead. Renaming the parameter would touch `analyzer_sql.py`,
    `calibration.py` and `budget_check` for a name that already reads
    correctly everywhere else — the parameter's real meaning is
    "independent baseline observations", and for reassure that unit is the
    import. `ReassureComparison.baseline_import_n` carries the honest name
    back out to callers instead.

    `UpdateCountChange` uses latest-vs-PREVIOUS (`points[-2]`), never the
    median baseline window — a median of `int | None` state values is
    meaningless. Two different baselines in one command is a real wart; it
    is the honest one (design `design.md:212-214`)."""

    if not points:
        return None

    latest = points[-1]
    baseline_points = points[:-1]
    baseline_import_n = len(baseline_points)

    duration_verdict = _classify_series(
        SERIES_DURATION,
        unit="ms",
        latest_metric=latest.entry.duration,
        baseline_points=baseline_points,
        select_metric=lambda point: point.entry.duration,
        threshold_pct=threshold_pct,
        floor=floors.get("ms", 0.0),
        baseline_import_n=baseline_import_n,
    )
    render_count_verdict = _classify_series(
        SERIES_RENDER_COUNT,
        unit="count",
        latest_metric=latest.entry.count,
        baseline_points=baseline_points,
        select_metric=lambda point: point.entry.count,
        threshold_pct=threshold_pct,
        floor=floors.get("count", 0.0),  # D7: absent key still yields 0.0
        baseline_import_n=baseline_import_n,
    )

    previous = points[-2] if len(points) >= 2 else None
    update_count = derive_update_count_change(
        previous.entry.initial_update_count if previous is not None else None,
        latest.entry.initial_update_count,
    )

    return ReassureComparison(
        name=latest.entry.name,
        latest=latest,
        baseline_import_n=baseline_import_n,
        verdicts=(duration_verdict, render_count_verdict),
        update_count=update_count,
    )


def _classify_series(
    metric_name: str,
    *,
    unit: str,
    latest_metric: HistoryMetric | None,
    baseline_points: Sequence[ReassureSeriesPoint],
    select_metric: Callable[[ReassureSeriesPoint], HistoryMetric | None],
    threshold_pct: float,
    floor: float,
    baseline_import_n: int,
) -> Verdict:
    """Reduces one series (`duration_ms` or `render_count`) across the
    baseline window to a single `Verdict` via `regression.classify` (A6:
    literal `higher_is_better=False`, never `default_higher_is_better()` —
    removes the latent un-namespaced-collision risk instead of documenting
    it). `select_metric` reads the already-reduced `HistoryMetric` off
    each point's `entry` (invariant I1 — never a raw sample array)."""

    baseline_p90s = [
        metric.p90
        for point in baseline_points
        if (metric := select_metric(point)) is not None and metric.p90 is not None
    ]
    baseline_value = statistics.median(baseline_p90s) if baseline_p90s else None
    latest_value = latest_metric.p90 if latest_metric is not None else None
    sample_n = latest_metric.n if latest_metric is not None else 0

    # Chronological per-import p90s (oldest first) + the latest value
    # appended last — feeds `Verdict.series`, the sparkline source. Plain
    # per-import values, never per-commit medians (I2).
    series = tuple(baseline_p90s) + ((latest_value,) if latest_value is not None else ())

    return regression.classify(
        metric_name,
        latest_value,
        baseline_value,
        unit=unit,
        higher_is_better=False,  # A6
        threshold_pct=threshold_pct,
        floor=floor,
        baseline_commit_n=baseline_import_n,
        sample_n=sample_n,
        min_n=MIN_BASELINE_IMPORTS,
        series=series,
    )
