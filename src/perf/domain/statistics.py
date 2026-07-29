"""Pure aggregation helpers `compare` needs beyond what the SQL view
already computes (design "Median location" decision — SQLite has no
`MEDIAN` aggregate, and median-of-per-commit-medians would nest ugly
window-rank SQL; this stays pure and hypothesis-testable instead).

No I/O, no adapter imports — see `.claude/skills/perf-cli-standards/
SKILL.md` rule 1.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def median(values: Sequence[float]) -> float:
    """Standard median: the middle value for odd `n`, the average of the
    two middle values for even `n` (matches `run_metric_summary.p50_ms`'s
    convention). Raises `ValueError` on an empty sequence — callers must
    never silently treat "no data" as a zero baseline."""

    if not values:
        raise ValueError("median() requires at least one value")
    if any(value is None for value in values):
        # Defensive backstop (FIX 1, PR-B review): a `None` slipping
        # through (e.g. an unfiltered NULL `p90_ms` from an n=1 run) must
        # raise a CLEAR error, never a bare `TypeError` from `sorted()`
        # comparing `None` to `float`. Callers (the analyzer) are
        # expected to filter `None`s before calling — this never fires
        # in the normal path.
        raise ValueError("median() does not accept None values — filter before calling")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile, `p` in `[0, 100]` — matches
    `run_metric_summary.p90_ms`'s nearest-rank convention (db/schema.sql).
    `n=1` returns that single value for any `p`; an all-equal sequence
    returns that value; raises `ValueError` on an empty sequence or an
    out-of-range `p`."""

    if not values:
        raise ValueError("percentile() requires at least one value")
    if not 0 <= p <= 100:
        raise ValueError("p must be within [0, 100]")
    ordered = sorted(values)
    n = len(ordered)
    rank = max(1, math.ceil(p / 100 * n))
    return float(ordered[min(rank, n) - 1])


# Consistency constant that scales the median absolute deviation to a robust
# standard-deviation estimate for normally-distributed data (the standard
# 1/Φ⁻¹(3/4) ≈ 1.4826). Used by the adaptive noise floor (anti-false-positive
# batch, Task 2) so a metric's own historical scatter — not just a static
# per-unit floor — sets how big a change must be before it counts.
_MAD_SCALE = 1.4826


def robust_noise(values: Sequence[float]) -> float:
    """Robust dispersion estimate: `1.4826 * MAD`, where MAD is the median of
    the absolute deviations from the series median. Robust to outliers (unlike
    a plain standard deviation) — one freak run cannot inflate the estimate.

    Well-defined and finite on every input: an empty series, a single value,
    and any zero-variance (all-equal) series all return `0.0`; a NaN is never
    produced for finite inputs. Callers gate on `>= 3` points before letting
    it widen a floor (spec Task 2) — with fewer points the estimate is too
    unstable to trust — but the function itself imposes no minimum."""

    if not values:
        return 0.0
    center = median(values)
    deviations = [abs(value - center) for value in values]
    return _MAD_SCALE * median(deviations)


def median_by_commit(points: Iterable[tuple[str, float]]) -> dict[str, float]:
    """Collapses repeated same-commit runs to exactly ONE median value per
    commit (spec "Baseline Correctness" — commit C with 3 recorded runs
    contributes one point, not 3). `points` is `(git_commit, value)` pairs
    in any order; the caller (`regression`/`calibration`) takes the median
    ACROSS the returned per-commit medians to get the final baseline."""

    by_commit: dict[str, list] = {}
    for commit, value in points:
        by_commit.setdefault(commit, []).append(value)
    return {commit: median(values) for commit, values in by_commit.items()}
