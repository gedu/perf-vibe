"""`--json` machine contract for `perfvibe reassure compare <name>` (SKILL
rule 6: "the machine contract is `--json` (carries `schema_version`); the
pretty view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract
test MUST fail on any `--json` shape change without a `schema_version`
bump.").

**THE LOAD-BEARING FACT ABOUT THIS COMMAND (D3, agent-facing)**: `reassure
compare` ALWAYS exits `0`, including on a confirmed regression — the exit
code carries NO verdict information. An agent MUST read this payload's
`verdicts` array (each entry's `status`) to learn the actual result; see
`AGENTS.md` and `CLAUDE.md` for the full agent-facing warning.

`schema_version=1`. Top-level FLAT dict with exactly EIGHT keys: `name`,
`latest_import_id` (the import `verdicts` were computed against),
`baseline_import_n` (IMPORTS, never commits — `ReassureComparison`'s
honest name for the naming friction `regression.classify`'s own
`baseline_commit_n` parameter carries, design "The Verdict Function"),
`verdicts` (a list, FIXED order — `duration_ms` then `render_count`, never
re-sorted — `ReassureComparison.verdicts` is already in that order and
this builder never touches it), and the THREE flat D5 keys:
`initial_update_count`, `baseline_initial_update_count` (both `int | None`
— `None` means "never measured", `0` means "measured, clean"; the two
facts must never collapse) and `initial_update_state`.

There is deliberately NO `*_delta_pct`/`*_pct` field anywhere for the
update count (D5 is a state transition, never a delta — design A13).
`None` survives serialization because Python `None` maps to JSON `null`
natively (`json_reporter`'s sanitizer only rewrites non-finite floats).

Each verdict entry mirrors `contracts/compare_v1.py`'s own per-verdict
shape (`metric`, `unit`, `direction`, `latest_value`, `baseline_value`,
`delta_pct`, `threshold_pct`, `floor`, `status`, `sample_n`,
`baseline_commit_n`) — this module owns that mapping independently rather
than importing `compare_v1`'s private `_verdict_payload`, the same way
`budget_check_v1.py` owns its own `_gated_verdict_payload` rather than
reusing `compare_v1`'s (spec "Five new `_v1` contracts... own contract
test with exact key set/count").

Mirrors `contracts/reassure_show_v1.py`'s pure-builder pattern: this
function accepts an already-computed `ReassureComparison` — it never calls
`Store.reassure_series` or `domain.reassure_compare.compare_series`
itself, and never decides which import is "latest" (the CLI's job, per
A8). Contains NO secrets — this module only ever receives a
`ReassureComparison`, never a request/env mapping."""

from __future__ import annotations

from typing import Any

from perf.domain.model import Verdict
from perf.domain.reassure_compare import ReassureComparison

__all__ = ["SCHEMA_VERSION", "build_reassure_compare_payload"]

SCHEMA_VERSION = 1


def _direction(verdict: Verdict) -> str:
    # ALWAYS the verdict's own field — the direction `regression.classify`
    # actually used (A6 sets it to the literal `False` for both reassure
    # series), never re-derived from `metric_name` (mirrors `compare_v1`'s
    # own audit-fixed `_direction`).
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


def build_reassure_compare_payload(*, comparison: ReassureComparison) -> dict[str, Any]:
    """Builds the stable `--json` verdict payload for `reassure compare
    <name>`. `comparison` is the already-computed `ReassureComparison`
    (`domain.reassure_compare.compare_series`'s return value, guaranteed
    non-`None` by the CLI's own unknown-name guard) — this builder only
    shapes the dict, it never classifies or resolves anything itself."""

    update_count = comparison.update_count
    return {
        "schema_version": SCHEMA_VERSION,
        "name": comparison.name,
        "latest_import_id": comparison.latest.import_id,
        "baseline_import_n": comparison.baseline_import_n,
        "verdicts": [_verdict_payload(verdict) for verdict in comparison.verdicts],
        "initial_update_count": update_count.latest,
        "baseline_initial_update_count": update_count.baseline,
        "initial_update_state": update_count.state,
    }
