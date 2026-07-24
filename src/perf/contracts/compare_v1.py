"""`--json` machine contract for `perf compare`'s verdict output (SKILL
rule 6: "the machine contract is `--json` (carries `schema_version`); the
pretty view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract
test MUST fail on any `--json` shape change without a `schema_version`
bump.").

`schema_version=1`. Stable, versioned, and lossless over `CompareResult`:
every per-metric `Verdict` (including `insufficient-data` ones) and the
always-on config sanity label (`CalibrationReport`, decision #58) are
included verbatim. `direction` is read straight off `Verdict.higher_is_
better` — the EXACT value `regression.classify` used to produce this
verdict's `status` (threaded through by `analyzer_sql.py`'s calls into
`classify`) — rather than re-derived from `metric_name` at serialization
time (audit fix: the two lookups could otherwise silently diverge if
`_HIGHER_IS_BETTER_METRICS` ever changes for a metric with old persisted
rows, or a per-metric override is ever introduced). Contains NO secrets —
this module only ever receives a `CompareResult`, never a request/env
mapping.
"""

from __future__ import annotations

from typing import Any

from perf.domain.calibration import CalibrationReport, MetricCalibration
from perf.domain.model import CompareResult, Verdict

__all__ = ["SCHEMA_VERSION", "build_compare_payload"]

SCHEMA_VERSION = 1


def _direction(verdict: Verdict) -> str:
    return "higher-is-better" if verdict.higher_is_better else "lower-is-better"


def _verdict_payload(verdict: Verdict) -> dict[str, Any]:
    return {
        "metric": verdict.metric_name,
        "unit": verdict.unit,
        "direction": _direction(verdict),
        "latest_value": verdict.latest_value,
        "baseline_value": verdict.baseline_value,
        "delta_pct": verdict.delta_pct,
        "threshold_pct": verdict.threshold_pct,
        "floor": verdict.floor,
        "status": verdict.status,
        "sample_n": verdict.sample_n,
        "baseline_commit_n": verdict.baseline_commit_n,
    }


def _metric_calibration_payload(metric_grade: MetricCalibration) -> dict[str, Any]:
    return {
        "metric": metric_grade.metric_name,
        "status": metric_grade.status,
        "flagged_count": metric_grade.flagged_count,
        "total_count": metric_grade.total_count,
    }


def _calibration_payload(report: CalibrationReport) -> dict[str, Any]:
    return {
        "status": report.status,
        "runs_flagged": report.runs_flagged,
        "runs_total": report.runs_total,
        "metrics": [_metric_calibration_payload(metric) for metric in report.metrics],
    }


def build_compare_payload(result: CompareResult) -> dict[str, Any]:
    """Builds the stable `--json` verdict payload for a `perf compare`
    invocation. Every field is sourced from `CompareResult` — the sanity
    label (`calibration`) is purely informational (spec "Config Sanity
    Label"): its presence never changes any verdict's `status` here."""

    verdicts: list[dict[str, Any]] = [_verdict_payload(verdict) for verdict in result.verdicts]
    return {
        "schema_version": SCHEMA_VERSION,
        "verdicts": verdicts,
        "calibration": _calibration_payload(result.calibration),
    }
