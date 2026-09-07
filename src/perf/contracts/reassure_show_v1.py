"""`--json` machine contract for `perfvibe reassure show <name>` (SKILL
rule 6: "the machine contract is `--json` (carries `schema_version`); the
pretty view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract
test MUST fail on any `--json` shape change without a `schema_version`
bump.").

`schema_version=1`. Top-level FLAT dict with exactly TEN keys — the one
entry `reassure show` reports, mirroring `reassure_entries_v1`'s
per-entry shape one level UP (no list, no `import_id`-repeated-per-entry
concern, since there is only ever one entry here): `import_id`, `name`,
`entry_type`, `runs` (declared), `duration`, `count` (each either `None`
or a nested `{p50, p90, n, unit}` dict, reusing `reassure_entries_v1.
_metric_payload`'s exact shape), plus the THREE D5 keys.

D5 (design "D5 — State Transition in Both Views", `design.md:385-393`):
`initial_update_count`, `baseline_initial_update_count` (both `int |
None` — `None` means "never measured", `0` means "measured, clean"; the
two facts must never collapse), and `initial_update_state` (one of
`'introduced'`, `'resolved'`, `'changed'`, `'unchanged'`, `'unknown'`,
via `domain.reassure_compare.derive_update_count_change` — the SAME
derivation `reassure compare` reuses later, PR4a). There is deliberately
NO `*_delta_pct`/`*_pct` field anywhere for the update count — D5 is a
state transition, never a delta (design A13).

`runs` (declared) is NEVER reconciled against `duration.n`/`count.n` here
— same no-second-source-of-truth rule `reassure_entries_v1` already
established; a `declared runs N != stored n M` line is the PRETTY view's
job (`cli/output/reassure_show_pretty.py`), not this builder's.

Mirrors `contracts/reassure_entries_v1.py`'s pure-builder pattern: this
function accepts an already-resolved `ReassureEntryRow` plus the
already-looked-up baseline `initial_update_count` — it never calls
`Store.reassure_entries`/`Store.reassure_series`/`Store.reassure_imports`
itself, and never decides which import is "latest" or "the immediately
preceding one" (the CLI's job, per D8/A14)."""

from __future__ import annotations

from typing import Any

from perf.domain.model import HistoryMetric, ReassureEntryRow
from perf.domain.reassure_compare import derive_update_count_change

__all__ = ["SCHEMA_VERSION", "build_reassure_show_payload"]

SCHEMA_VERSION = 1


def _metric_payload(metric: HistoryMetric | None) -> dict[str, Any] | None:
    if metric is None:
        return None
    return {
        "p50": metric.p50,
        "p90": metric.p90,
        "n": metric.n,
        "unit": metric.unit,
    }


def build_reassure_show_payload(
    *,
    import_id: int,
    entry: ReassureEntryRow,
    baseline_initial_update_count: int | None,
) -> dict[str, Any]:
    """Builds the stable `--json` payload for `reassure show <name>`.
    `entry` is the already-resolved `ReassureEntryRow` for `name` in the
    target import (D8 default or `--import <id>` override — the CLI's
    job); `baseline_initial_update_count` is the already-looked-up value
    from the immediately preceding import that also contains `name` (or
    `None` when there is no such import — "no prior diagnostic"). This
    builder only shapes the dict and derives `initial_update_state`; it
    never fetches or resolves either value itself."""

    latest_initial_update_count = entry.initial_update_count
    change = derive_update_count_change(baseline_initial_update_count, latest_initial_update_count)

    return {
        "schema_version": SCHEMA_VERSION,
        "import_id": import_id,
        "name": entry.name,
        "entry_type": entry.entry_type,
        "runs": entry.runs,
        "duration": _metric_payload(entry.duration),
        "count": _metric_payload(entry.count),
        "initial_update_count": latest_initial_update_count,
        "baseline_initial_update_count": baseline_initial_update_count,
        "initial_update_state": change.state,
    }
