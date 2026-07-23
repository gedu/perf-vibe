"""Pure config-sanity calibration (design Rev 2/3 "Calibration contract",
decision #58 — the always-on, honest-degenerate label). Grades the ACTIVE
`threshold_pct`/floor against the flow's OBSERVED walk-forward delta
distribution; NEVER changes a `Verdict.status` or the exit code — this is
purely informational (spec "Config Sanity Label").

No I/O, no adapter imports — see `.claude/skills/perf-cli-standards/
SKILL.md` rule 1.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from perf.domain import regression
from perf.domain.statistics import median, median_by_commit

STATUS_REASONABLE = "reasonable"
STATUS_TOO_LOOSE = "too-loose"
STATUS_TOO_STRICT = "too-strict"
STATUS_INSUFFICIENT_DATA = "insufficient-data"

_MIN_COMMITS_FOR_GRADE = 2

RunPointRow = tuple[str, float, str]  # (git_commit, value, started_at)


@dataclass(frozen=True)
class MetricCalibration:
    """One metric's config-sanity grade (design "Calibration contract")."""

    metric_name: str
    status: str  # 'reasonable' | 'too-loose' | 'too-strict' | 'insufficient-data'
    flagged_count: int
    total_count: int
    max_abs: float
    noise_pct: float
    flagged_commits: Sequence[str] = ()


@dataclass(frozen=True)
class CalibrationReport:
    """Aggregate across every metric (design "grade_all")."""

    metrics: Sequence[MetricCalibration]
    status: str
    runs_flagged: int
    runs_total: int


def grade(
    per_run_points: Sequence[RunPointRow],
    *,
    metric_name: str,
    unit: str,
    higher_is_better: bool,
    floor: float,
    threshold_pct: float,
) -> MetricCalibration:
    """Grades ONE metric's history (design "Calibration contract" steps
    1-5): (1) collapse to `commit_medians` ordered by time, (2) walk
    forward one commit at a time — each commit is compared against the
    median-by-commit baseline of every EARLIER commit, yielding a signed
    delta per step, (3) within-commit repeats give the run-to-run noise
    (fallback: adjacent-step deltas when no repeats exist), (4)
    `max_abs` = the largest observed `|delta|`, (5) `flagged` reuses
    `regression.classify`'s worse-AND-floor-AND-threshold rule.

    Label: `too-loose` IFF there EXISTS a historical walk-forward step
    whose `|delta_pct| >= threshold_pct` (the percentage threshold WOULD
    have flagged it) BUT whose `|delta_abs| < floor` (the floor actually
    SUPPRESSED that step) — i.e. the floor is provably too big for this
    metric's scale, evidenced by a concrete suppressed step. A dead-stable
    baseline (no step ever reaches `threshold_pct`) is NEVER `too-loose`
    just because the floor happens to exceed its tiny deltas — that would
    LIE about a healthy history (decision #58 anti-lying-label; PR-C
    review fix — the previous `floor >= max_abs` rule mislabelled every
    stable baseline `too-loose`, since a calm history trivially has
    `max_abs` below any nonzero floor). Else `too-strict` IFF
    `threshold_pct < noise_pct` (normal noise alone would flag); else
    `reasonable` — reporting the exact count, never lying about a
    legitimately stable (or improvement-dominated) history.

    `per_run_points` are the exact `(git_commit, value, started_at)` rows
    `baseline_points` returns — pre-collapse, any order.
    """

    by_commit: dict[str, list[float]] = {}
    earliest_seen: dict[str, str] = {}
    for commit, value, started_at in per_run_points:
        by_commit.setdefault(commit, []).append(value)
        if commit not in earliest_seen or started_at < earliest_seen[commit]:
            earliest_seen[commit] = started_at

    commit_medians = median_by_commit((commit, value) for commit, value, _ in per_run_points)
    ordered_commits = sorted(commit_medians, key=lambda c: earliest_seen[c])

    if len(ordered_commits) < _MIN_COMMITS_FOR_GRADE:
        return MetricCalibration(
            metric_name=metric_name,
            status=STATUS_INSUFFICIENT_DATA,
            flagged_count=0,
            total_count=0,
            max_abs=0.0,
            noise_pct=0.0,
        )

    ordered_values = [commit_medians[commit] for commit in ordered_commits]

    deltas_abs: list[float] = []
    deltas_pct: list[float] = []
    flagged_commits: list[str] = []
    for i in range(1, len(ordered_values)):
        base = median(ordered_values[:i])
        latest = ordered_values[i]
        delta_abs = latest - base
        if base == 0:
            delta_pct = 0.0 if delta_abs == 0 else float("inf")
        else:
            delta_pct = delta_abs / base * 100.0
        deltas_abs.append(delta_abs)
        deltas_pct.append(delta_pct)

        verdict = regression.classify(
            metric_name,
            latest,
            base,
            unit=unit,
            higher_is_better=higher_is_better,
            threshold_pct=threshold_pct,
            floor=floor,
            baseline_commit_n=i,
            sample_n=i,
            min_n=1,
        )
        if verdict.status == regression.STATUS_REGRESSION:
            flagged_commits.append(ordered_commits[i])

    max_abs = max((abs(delta) for delta in deltas_abs), default=0.0)

    noise_samples = [
        abs((value - commit_medians[commit]) / commit_medians[commit] * 100.0)
        for commit, values in by_commit.items()
        if len(values) > 1 and commit_medians[commit] != 0
        for value in values
    ]
    if not noise_samples:
        noise_samples = [abs(pct) for pct in deltas_pct if math.isfinite(pct)]
    noise_pct = median(noise_samples) if noise_samples else 0.0

    floor_suppressed_a_significant_step = any(
        abs(delta_pct) >= threshold_pct and abs(delta_abs) < floor
        for delta_abs, delta_pct in zip(deltas_abs, deltas_pct)
    )

    if floor_suppressed_a_significant_step:
        status = STATUS_TOO_LOOSE
    elif threshold_pct < noise_pct:
        status = STATUS_TOO_STRICT
    else:
        status = STATUS_REASONABLE

    return MetricCalibration(
        metric_name=metric_name,
        status=status,
        flagged_count=len(flagged_commits),
        total_count=len(ordered_values) - 1,
        max_abs=max_abs,
        noise_pct=noise_pct,
        flagged_commits=tuple(flagged_commits),
    )


def grade_all(
    per_metric_points: Mapping[str, Sequence[RunPointRow]],
    *,
    floors: Mapping[str, float],
    threshold_pct: float,
    units: Mapping[str, str],
    higher_is_better: Mapping[str, bool],
) -> CalibrationReport:
    """Aggregates `grade()` across every metric (design "grade_all").

    Overall `status` precedence (honest — never a reassuring label without
    evidence): `too-strict` > `too-loose` > `reasonable` > `insufficient-data`.
    A degenerate config outranks all; `reasonable` is reported ONLY when at
    least one metric had enough history to grade; if NOTHING could be graded
    (every metric `insufficient-data`), the aggregate is `insufficient-data`,
    NOT `reasonable`. Between the two degenerate states, `too-strict` (cries
    wolf) is reported first, deterministically; each metric's own status is
    always available in `metrics` for per-metric rendering. `runs_flagged` is
    the count of DISTINCT historical commits where ANY metric would flag, out
    of every distinct commit observed across all metrics."""

    metrics: list[MetricCalibration] = []
    all_commits: set = set()
    flagged_union: set = set()

    for metric_name, points in per_metric_points.items():
        unit = units.get(metric_name, "ms")
        floor = floors.get(unit, 0.0)
        metric_grade = grade(
            points,
            metric_name=metric_name,
            unit=unit,
            higher_is_better=higher_is_better.get(metric_name, False),
            floor=floor,
            threshold_pct=threshold_pct,
        )
        metrics.append(metric_grade)
        flagged_union.update(metric_grade.flagged_commits)
        all_commits.update(commit for commit, _, _ in points)

    statuses = {metric_grade.status for metric_grade in metrics}
    if STATUS_TOO_STRICT in statuses:
        worst_status = STATUS_TOO_STRICT
    elif STATUS_TOO_LOOSE in statuses:
        worst_status = STATUS_TOO_LOOSE
    elif STATUS_REASONABLE in statuses:
        worst_status = STATUS_REASONABLE
    else:
        # No metric had enough history to grade — surface that honestly
        # instead of a reassuring "reasonable" with zero evidence.
        worst_status = STATUS_INSUFFICIENT_DATA

    return CalibrationReport(
        metrics=tuple(metrics),
        status=worst_status,
        runs_flagged=len(flagged_union),
        runs_total=len(all_commits),
    )
