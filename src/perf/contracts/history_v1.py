"""`--json` machine contract for `perf history <flow>` — the per-flow
historical series that is the POINT of this command (SKILL rule 6: "the
machine contract is `--json` (carries `schema_version`); the pretty view is
lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract test MUST fail on
any `--json` shape change without a `schema_version` bump.").

`schema_version=1`. Stable, versioned, and lossless over the `history`
read model (`Sequence[HistoryRun]`): every run in the queried window
(OLDEST→NEWEST) with its identity metadata and every metric's per-run
{p50, p90, n, unit} summary — across BOTH metric families. Non-finite
floats never reach a consumer as invalid JSON: `cli/output/json_reporter`'s
sanitizer maps them to `null` (rendered via `render_json`, exactly like
`compare`). Contains NO secrets — this module only ever receives the
already-computed `HistoryRun` value objects plus the flow/device/mode
labels the CLI resolved.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from perf.domain.model import HistoryMetric, HistoryRun

__all__ = ["SCHEMA_VERSION", "build_history_payload"]

SCHEMA_VERSION = 1


def _metric_payload(metric: HistoryMetric) -> dict[str, Any]:
    return {
        "p50": metric.p50,
        "p90": metric.p90,
        "n": metric.n,
        "unit": metric.unit,
    }


def _run_payload(run: HistoryRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "commit": run.git_commit,
        "source": run.source,
        "metrics": {metric.metric_name: _metric_payload(metric) for metric in run.metrics},
    }


def build_history_payload(
    flow: str, device: str, mode: str, runs: Sequence[HistoryRun]
) -> dict[str, Any]:
    """Builds the stable `--json` historical-series payload for a `perf
    history <flow>` invocation. `runs` is already OLDEST→NEWEST (the store's
    natural chart order) and already restricted to the requested metric when
    `--metric` was passed — this builder only shapes, it never filters or
    reorders."""

    return {
        "schema_version": SCHEMA_VERSION,
        "flow": flow,
        "device": device,
        "mode": mode,
        "runs": [_run_payload(run) for run in runs],
    }
