"""`--json` machine contract for `perf budget-check`'s gate output (design
§8, decision D1, SKILL rule 6: "the machine contract is `--json` (carries
`schema_version`); the pretty view is lossy and MUST NEVER be parsed";
SKILL rule 8: "A contract test MUST fail on any `--json` shape change
without a `schema_version` bump.").

`schema_version=1`. FLATTENED shape, OWN and INDEPENDENT of `compare_v1`:
a top-level `gate_status` (`"pass" | "fail" | "skipped"`) plus a flat
`verdicts[]` list where each entry carries compare's per-metric verdict
fields PLUS an added `gated: bool`. The payload does NOT nest `compare_v1`'s
shape under a key. `series_points` and `calibration` are DELIBERATELY
ABSENT — both are render-time/informational concerns, not part of the lean,
gate-first machine contract (design §8). Contains NO secrets — this module
only ever receives a `BudgetVerdict`, never a request/env mapping.
"""

from __future__ import annotations

from typing import Any

from perf.domain.model import BudgetVerdict, GatedVerdict, default_higher_is_better

__all__ = ["SCHEMA_VERSION", "build_payload"]

SCHEMA_VERSION = 1


def _direction(metric_name: str) -> str:
    return "higher-is-better" if default_higher_is_better(metric_name) else "lower-is-better"


def _gated_verdict_payload(gv: GatedVerdict) -> dict[str, Any]:
    verdict = gv.verdict
    return {
        "metric": verdict.metric_name,
        "unit": verdict.unit,
        "direction": _direction(verdict.metric_name),
        "latest_value": verdict.latest_value,
        "baseline_value": verdict.baseline_value,
        "delta_pct": verdict.delta_pct,
        "threshold_pct": verdict.threshold_pct,
        "floor": verdict.floor,
        "status": verdict.status,
        "gated": gv.gated,
        "sample_n": verdict.sample_n,
        "baseline_commit_n": verdict.baseline_commit_n,
    }


def build_payload(bv: BudgetVerdict) -> dict[str, Any]:
    """Builds the stable, flattened `--json` gate payload for a `perf
    budget-check` invocation (design §8). `series_points`/`calibration`
    are deliberately excluded — the gate contract stays lean and
    self-contained (pinned by `tests/contract/test_budget_check_v1.py`,
    independent of `compare_v1`'s own contract test)."""

    return {
        "schema_version": SCHEMA_VERSION,
        "gate_status": bv.gate_status,
        "strict": bv.strict,
        "offending_metrics": list(bv.offending_metrics),
        "verdicts": [_gated_verdict_payload(gv) for gv in bv.gated_verdicts],
    }
