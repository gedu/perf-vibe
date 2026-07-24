"""Pure, direction-aware regression classification (design Rev 3
"Direction-Aware Classification" / "Interfaces / Contracts" + decision
#39 — the FPS-drop inversion bug: a naive "bigger number = worse" rule
is WRONG for higher-is-better metrics like FPS).

No I/O, no adapter imports — see `.claude/skills/perf-cli-standards/
SKILL.md` rule 1.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from perf.domain.model import SeriesPoint, Verdict

STATUS_IMPROVEMENT = "improvement"
STATUS_STABLE = "stable"
STATUS_REGRESSION = "regression"
STATUS_INSUFFICIENT_DATA = "insufficient-data"


def classify(
    metric_name: str,
    latest: float | None,
    baseline: float | None,
    *,
    unit: str,
    higher_is_better: bool,
    threshold_pct: float,
    floor: float,
    baseline_commit_n: int,
    sample_n: int,
    min_n: int,
    series: Sequence[float] = (),
    series_points: Sequence[SeriesPoint] = (),
) -> Verdict:
    """Direction-aware classify (design "Interfaces / Contracts").

    `insufficient-data` when there is no latest value, no baseline, too
    few baseline commits, or too few post-warm-up samples — NEVER
    silently `stable` (spec "Insufficient-Data Classification", corner
    cases C1/C3/C5). Otherwise BOTH the absolute `floor` AND
    `threshold_pct` must be exceeded before flagging `improvement` or
    `regression` (spec "Threshold and Absolute Floor"); `higher_is_better`
    decides which delta sign is "worse" (decision #39). `baseline == 0`
    is guarded — no `ZeroDivisionError` (corner case C4).

    `series_points` (budget-check design §5) is threaded straight onto the
    returned `Verdict` on BOTH the insufficient-data early return and the
    normal-classification return — additive, defaults to `()` so every
    existing caller keeps working unchanged.

    `higher_is_better` (audit fix) is likewise echoed onto the returned
    `Verdict` on both paths — it is the SAME input this function used to
    decide `worse`/`better`, so the verdict carries the exact direction it
    was classified with rather than leaving `--json` serialization to
    re-derive it by metric name later.
    """

    if latest is None or baseline is None or baseline_commit_n < min_n or sample_n < min_n:
        return Verdict(
            metric_name=metric_name,
            delta_pct=0.0,
            threshold_pct=threshold_pct,
            status=STATUS_INSUFFICIENT_DATA,
            latest_value=latest,
            baseline_value=baseline,
            unit=unit,
            sample_n=sample_n,
            baseline_commit_n=baseline_commit_n,
            series=tuple(series),
            floor=floor,
            series_points=tuple(series_points),
            higher_is_better=higher_is_better,
        )

    delta = latest - baseline
    if baseline == 0:
        rel_pct = 0.0 if delta == 0 else math.copysign(float("inf"), delta)
    else:
        rel_pct = (delta / baseline) * 100.0

    exceeds_floor = abs(delta) >= floor
    exceeds_threshold = abs(rel_pct) >= threshold_pct

    worse = delta < 0 if higher_is_better else delta > 0
    better = delta > 0 if higher_is_better else delta < 0

    if worse and exceeds_floor and exceeds_threshold:
        status = STATUS_REGRESSION
    elif better and exceeds_floor and exceeds_threshold:
        status = STATUS_IMPROVEMENT
    else:
        status = STATUS_STABLE

    return Verdict(
        metric_name=metric_name,
        delta_pct=rel_pct,
        threshold_pct=threshold_pct,
        status=status,
        latest_value=latest,
        baseline_value=baseline,
        unit=unit,
        sample_n=sample_n,
        baseline_commit_n=baseline_commit_n,
        series=tuple(series),
        floor=floor,
        series_points=tuple(series_points),
        higher_is_better=higher_is_better,
    )
