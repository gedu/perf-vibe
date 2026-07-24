"""`Analyzer` Protocol adapter — the ONE `Analyzer` implementation (design
"Analyzer factory" decision: rule of three, single implementation, plain
factory — no name-keyed map). Composes bounded, `?`-bound SQL reads from
`SqliteStore` (batched per metric-family, `idx_run_baseline`-backed,
PR-B/Rev 3) with PR-A's pure domain math (`statistics`, `regression`,
`calibration`) into one `CompareResult`. No SQL lives here — every
statement is in `adapters/store_sqlite.py`; this module only orchestrates
(design "Data Flow").

Warm-up discard `K` (spec "Warm-Up Discard Asymmetry") applies ONLY to
`system_sample`-derived metrics, which carry an iteration `idx`; marker/
`measure` metrics have no ordinal and are NEVER warm-up-filtered — an
explicit branch below, never silent (decision #53.3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from perf.adapters.store_sqlite import (
    BaselineSystemSamplePoint,
    LatestRun,
    SqliteStore,
    SystemSampleRawPoint,
)
from perf.domain import calibration, regression, statistics
from perf.domain.model import (
    CompareResult,
    RunPoint,
    SeriesPoint,
    Verdict,
    default_higher_is_better,
)

# Fixed unit metadata for the closed set of `system_sample` aggregate
# fields (design "Interfaces / Contracts"; decision #58 floors keyed by
# unit). Deliberately NOT read from the `metric` table: `run`'s ingestion
# (`SqliteStore._upsert_metrics`) does not thread a per-field unit for
# system_sample metrics (it always defaults to 'ms' there — out of THIS
# slice's scope to change, since that is `run`'s write path). Compare
# supplies the CORRECT unit itself so `floors` (ms/mb/pct/fps) apply
# meaningfully.
_SYSTEM_SAMPLE_UNITS: Mapping[str, str] = {
    "total_time_ms": "ms",
    "start_time_ms": "ms",
    "fps_avg": "fps",
    "fps_min": "fps",
    "ram_avg_mb": "mb",
    "ram_peak_mb": "mb",
    "cpu_avg_pct": "pct",
    "cpu_peak_pct": "pct",
}

_PERCENTILE = 90.0


class SqlAnalyzer:
    """`Analyzer` Protocol implementation (`domain/ports.py`). Typed
    against the concrete `SqliteStore` — it is the only `Store`
    implementation (rule of three: no speculative Protocol dispatch for a
    single adapter)."""

    def __init__(
        self,
        store: SqliteStore,
        *,
        threshold_pct: float,
        floors: Mapping[str, float],
        min_baseline_commits: int,
        warmup_k: int,
        baseline_n: int,
    ) -> None:
        self._store = store
        self._threshold_pct = threshold_pct
        self._floors = dict(floors)
        self._min_baseline_commits = min_baseline_commits
        self._warmup_k = warmup_k
        self._baseline_n = baseline_n

    def compare_latest(
        self, flow_name: str, device_key: str, mode: str = "warm"
    ) -> CompareResult | None:
        """`None` when the flow/device/mode has no runs at all (corner
        case C2/C7 — the CLI, PR-C, maps this to the usage-error exit).
        Otherwise every metric observed in the LATEST run gets a
        `Verdict` (possibly `insufficient-data` — corner cases
        C1/C3/C4/C5/C7/C8/C9); a metric that exists only in the baseline,
        never in the latest run, is silently skipped (C6) — no crash."""

        latest: LatestRun | None = self._store.latest_run(flow_name, device_key, mode)
        if latest is None:
            return None

        verdicts: list[Verdict] = []
        per_metric_points: dict[str, Sequence[calibration.RunPointRow]] = {}
        units: dict[str, str] = {}
        higher_is_better: dict[str, bool] = {}

        self._compare_measure_family(
            flow_name,
            device_key,
            mode,
            latest,
            verdicts,
            per_metric_points,
            units,
            higher_is_better,
        )
        self._compare_system_sample_family(
            flow_name,
            device_key,
            mode,
            latest,
            verdicts,
            per_metric_points,
            units,
            higher_is_better,
        )

        report = calibration.grade_all(
            per_metric_points,
            floors=self._floors,
            threshold_pct=self._threshold_pct,
            units=units,
            higher_is_better=higher_is_better,
        )
        return CompareResult(verdicts=tuple(verdicts), calibration=report)

    # ----- measure family (markers; `run_metric_summary`; no warm-up) -----

    def _compare_measure_family(
        self,
        flow_name: str,
        device_key: str,
        mode: str,
        latest: LatestRun,
        verdicts: list[Verdict],
        per_metric_points: dict[str, Sequence[calibration.RunPointRow]],
        units: dict[str, str],
        higher_is_better: dict[str, bool],
    ) -> None:
        latest_points = self._store.latest_measure_summary(latest.run_id)
        if not latest_points:
            return

        baseline_rows = self._store.baseline_measure_points(
            flow_name, device_key, mode, latest.git_commit, self._baseline_n
        )
        baseline_by_metric = _group_run_points_by_metric(baseline_rows)

        for point in latest_points:
            points = baseline_by_metric.get(point.metric_name, ())
            # FIX 1 (BLOCKER, PR-B review) defensive filter: `value` should
            # never be `None` here (the store's `p90_ms IS NOT NULL` filter
            # already excludes n=1/NULL-p90 runs), but this explicit filter
            # is the primary guard against ever feeding a `None` into
            # `median_by_commit`/`median` — those stay strict and raise
            # rather than silently accept it (see `domain/statistics`).
            non_null_points = [
                (commit, value, started_at)
                for commit, value, started_at in points
                if value is not None
            ]
            commit_medians = statistics.median_by_commit(
                (commit, value) for commit, value, _ in non_null_points
            )
            baseline_value = (
                statistics.median(list(commit_medians.values())) if commit_medians else None
            )

            verdicts.append(
                regression.classify(
                    point.metric_name,
                    point.p90_ms,
                    baseline_value,
                    unit=point.unit,
                    higher_is_better=point.higher_is_better,
                    threshold_pct=self._threshold_pct,
                    floor=self._floors.get(point.unit, 0.0),
                    baseline_commit_n=len(commit_medians),
                    sample_n=point.sample_n,
                    min_n=self._min_baseline_commits,
                    series=_sparkline_series(non_null_points, point.p90_ms),
                    series_points=_series_points(non_null_points, point.p90_ms, latest.git_commit),
                )
            )

            per_metric_points[point.metric_name] = points
            units[point.metric_name] = point.unit
            higher_is_better[point.metric_name] = point.higher_is_better

    # ----- system_sample family (Flashlight; warm-up K applies HERE ONLY) -----

    def _compare_system_sample_family(
        self,
        flow_name: str,
        device_key: str,
        mode: str,
        latest: LatestRun,
        verdicts: list[Verdict],
        per_metric_points: dict[str, Sequence[calibration.RunPointRow]],
        units: dict[str, str],
        higher_is_better: dict[str, bool],
    ) -> None:
        latest_raw = self._store.latest_system_sample_points(latest.run_id)
        if not latest_raw:
            return

        baseline_raw = self._store.baseline_system_sample_points(
            flow_name, device_key, mode, latest.git_commit, self._baseline_n
        )

        latest_by_metric = _collapse_latest_system_sample(latest_raw, self._warmup_k)
        baseline_by_metric = _collapse_baseline_system_sample(baseline_raw, self._warmup_k)

        for metric_name, (latest_value, sample_n) in latest_by_metric.items():
            unit = _SYSTEM_SAMPLE_UNITS.get(metric_name, "ms")
            better_when_higher = default_higher_is_better(metric_name)

            points = baseline_by_metric.get(metric_name, ())
            commit_medians = statistics.median_by_commit(
                (commit, value) for commit, value, _ in points
            )
            baseline_value = (
                statistics.median(list(commit_medians.values())) if commit_medians else None
            )

            verdicts.append(
                regression.classify(
                    metric_name,
                    latest_value,
                    baseline_value,
                    unit=unit,
                    higher_is_better=better_when_higher,
                    threshold_pct=self._threshold_pct,
                    floor=self._floors.get(unit, 0.0),
                    baseline_commit_n=len(commit_medians),
                    sample_n=sample_n,
                    min_n=self._min_baseline_commits,
                    series=_sparkline_series(points, latest_value),
                    series_points=_series_points(points, latest_value, latest.git_commit),
                )
            )

            per_metric_points[metric_name] = points
            units[metric_name] = unit
            higher_is_better[metric_name] = better_when_higher


def _ordered_commit_medians(
    points: Sequence[tuple[str, float, str]],
) -> list[tuple[str, float]]:
    """Chronological (oldest-first) `(commit, median)` pairs, one per
    distinct baseline commit — the SINGLE ordering source both
    `_sparkline_series` (bare floats, `Verdict.series`) and
    `_series_points` (labeled `SeriesPoint`s, `Verdict.series_points`)
    derive from (budget-check design risk #1: factored once so the two
    carriers cannot drift out of sync, not merely tested-against)."""

    earliest_seen: dict[str, str] = {}
    values_by_commit: dict[str, list[float]] = {}
    for commit, value, started_at in points:
        values_by_commit.setdefault(commit, []).append(value)
        if commit not in earliest_seen or started_at < earliest_seen[commit]:
            earliest_seen[commit] = started_at

    ordered_commits = sorted(values_by_commit, key=lambda commit: earliest_seen[commit])
    return [(commit, statistics.median(values_by_commit[commit])) for commit in ordered_commits]


def _sparkline_series(
    points: Sequence[tuple[str, float, str]], latest_value: float | None
) -> tuple[float, ...]:
    """Chronological per-commit baseline medians (oldest first) + the
    LATEST run's own value appended last — feeds `Verdict.series`, which
    `cli/output/compare_pretty.py`'s sparkline renders (PR-C, design
    "UX"). Reuses the SAME `points` rows already read for the baseline
    and calibration (design "One query, two consumers") — no new query.
    `latest_value=None` (e.g. a fully warm-up-dropped metric) contributes
    nothing extra; an empty/absent baseline yields an empty or
    single-point series, which the renderer's sparkline handles without
    crashing (spec 'Pretty-Output UX' sparkline edges)."""

    if not points:
        return (latest_value,) if latest_value is not None else ()

    series = [median for _commit, median in _ordered_commit_medians(points)]
    if latest_value is not None:
        series.append(latest_value)
    return tuple(series)


def _series_points(
    points: Sequence[tuple[str, float, str]],
    latest_value: float | None,
    latest_commit: str | None,
) -> tuple[SeriesPoint, ...]:
    """Labeled parallel carrier to `_sparkline_series` (budget-check design
    §5, decision D7): the SAME chronological ordering (`_ordered_commit_
    medians`), each point paired with its originating commit, plus a final
    `SeriesPoint(latest_commit, latest_value)` when `latest_value is not
    None` — mirrors `_sparkline_series`'s own latest-append guard exactly,
    so `len(series) == len(series_points)` and per-index values match by
    construction (design risk #1). `latest_commit` is `latest.git_commit`,
    which is `None` when a run was persisted with no resolvable git repo
    (`BashRunContextProvider` degrades gracefully rather than raising) —
    that edge case labels the point with `""` rather than dropping it, so
    the length parity invariant holds even then."""

    commit_label = latest_commit if latest_commit is not None else ""

    if not points:
        return (
            (SeriesPoint(commit=commit_label, value=latest_value),)
            if (latest_value is not None)
            else ()
        )

    series_points = [
        SeriesPoint(commit=commit, value=median)
        for commit, median in _ordered_commit_medians(points)
    ]
    if latest_value is not None:
        series_points.append(SeriesPoint(commit=commit_label, value=latest_value))
    return tuple(series_points)


def _group_run_points_by_metric(
    rows: Sequence[RunPoint],
) -> dict[str, list[tuple[str, float, str]]]:
    grouped: dict[str, list[tuple[str, float, str]]] = {}
    for row in rows:
        grouped.setdefault(row.metric_name, []).append((row.git_commit, row.value, row.started_at))
    return grouped


def _collapse_latest_system_sample(
    rows: Sequence[SystemSampleRawPoint], warmup_k: int
) -> dict[str, tuple[float | None, int]]:
    """Groups the LATEST run's raw per-iteration rows by metric, drops
    `idx < warmup_k` (warm-up asymmetry: `system_sample` ONLY), then
    reduces to one p90 value + the post-warm-up sample count per metric.

    A metric that HAD raw rows but loses every one of them to the
    warm-up drop (e.g. a single-iteration run with `warmup_k=1`) still
    gets an entry here — `(None, 0)` — so `compare_latest` still emits an
    `insufficient-data` `Verdict` for it (`classify`'s `sample_n < min_n`
    guard) instead of silently DROPPING the metric entirely, which would
    look identical to the metric never having existed (C6)."""

    by_metric: dict[str, list[float]] = {}
    seen_metrics: set = set()
    for row in rows:
        seen_metrics.add(row.metric_name)
        if row.iteration_idx < warmup_k:
            continue
        by_metric.setdefault(row.metric_name, []).append(row.value)

    result: dict[str, tuple[float | None, int]] = {}
    for metric_name in seen_metrics:
        values = by_metric.get(metric_name, [])
        if values:
            result[metric_name] = (statistics.percentile(values, _PERCENTILE), len(values))
        else:
            result[metric_name] = (None, 0)
    return result


def _collapse_baseline_system_sample(
    rows: Sequence[BaselineSystemSamplePoint], warmup_k: int
) -> dict[str, list[tuple[str, float, str]]]:
    """Same warm-up-drop as `_collapse_latest_system_sample`, but grouped
    per (metric, run) across the whole baseline window, reducing each RUN
    to one percentile point — yields `(git_commit, value, started_at)`
    PER RUN so `median_by_commit` can still collapse repeated same-commit
    runs afterwards (spec 'Baseline Correctness')."""

    by_metric_run: dict[tuple[str, int], list[float]] = {}
    meta: dict[tuple[str, int], tuple[str, str]] = {}
    for row in rows:
        if row.iteration_idx < warmup_k:
            continue
        key = (row.metric_name, row.run_id)
        by_metric_run.setdefault(key, []).append(row.value)
        meta[key] = (row.git_commit, row.started_at)

    result: dict[str, list[tuple[str, float, str]]] = {}
    for (metric_name, _run_id), values in by_metric_run.items():
        if not values:
            continue
        commit, started_at = meta[(metric_name, _run_id)]
        value = statistics.percentile(values, _PERCENTILE)
        result.setdefault(metric_name, []).append((commit, value, started_at))
    return result
