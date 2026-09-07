"""`--json` machine contract for `perfvibe reassure entries <import-id>`
(SKILL rule 6: "the machine contract is `--json` (carries
`schema_version`); the pretty view is lossy and MUST NEVER be parsed";
SKILL rule 8: "A contract test MUST fail on any `--json` shape change
without a `schema_version` bump.").

`schema_version=1`. Top-level FLAT dict with exactly THREE keys —
`schema_version`, `import_id`, `entries` (a list of per-entry dicts) —
mirroring `reassure_list_v1`'s `{schema_version, imports: [...]}` shape at
one level deeper: `entries` reports every `reassure_entry` row for ONE
import (spec "reassure entries <import-id> — One Import's Entries").

Each per-entry dict carries exactly FIVE keys: `name`, `entry_type`,
`runs` (the file-DECLARED count), `duration`, `count`. `duration`/`count`
are each EITHER `None` (that series had zero stored samples for this
entry — invariant I1: "no series", never a zero-valued metric standing in
for it) OR a nested `{p50, p90, n, unit}` dict, mirroring
`history_v1._metric_payload`'s exact shape. The two series are NEVER
zipped, paired, or forced to agree — `duration.n` and `count.n` are
independently whatever the store's two independent reduction queries
found (design "Load-Bearing Invariant" / I1).

`runs` is the file's DECLARED count and is NEVER reconciled against
`duration.n`/`count.n` here — a mismatch between the two is a real,
surfaceable fact (a truncated `.perf` file), not something this builder
computes a "corrected" answer for. A consumer already holding all three
numbers can compare them itself in one line; persisting a
`runs_match`/`declared_actual_mismatch` field would repeat the exact
no-second-source-of-truth problem `reassure_list_v1` already refused for
`ordering_key`/`ordered_at`.

Deliberately absent from each per-entry dict, by design:
- `entry_id` — the `reassure_entry` table's internal primary key. Every
  other reassure command that looks an entry up (`show`/`history`/
  `compare`) does so by `name`, never by this id, so it carries no
  external meaning and nothing downstream needs it.
- `initial_update_count` — `ReassureEntryRow` carries it (`domain/
  model.py`), but spec "reassure entries <import-id>" names only
  `name`/`entry_type`/`runs`/each series' `n`. Its D5 state-transition
  rendering ("extra mount render introduced") is `reassure show`'s own
  requirement (spec "reassure show <name>"), a later slice with its own
  contract — exposing it here, ahead of that slice's actual consumer,
  would be carrying a fact this command makes no use of.
- `import_id` PER ENTRY — already the top-level payload key; repeating it
  on every entry is a pure derived redundancy.

Mirrors `contracts/reassure_list_v1.py`'s and `contracts/history_v1.py`'s
pure-builder pattern: `build_reassure_entries_payload` accepts an
already-fetched `Sequence[ReassureEntryRow]` and shapes the dict — it
never calls `Store.reassure_entries`/`Store.reassure_import_exists`
itself, and never filters, sorts, or validates the `import_id` (the CLI's
job, via the store's own existence check)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from perf.domain.model import HistoryMetric, ReassureEntryRow

__all__ = ["SCHEMA_VERSION", "build_reassure_entries_payload"]

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


def _entry_payload(row: ReassureEntryRow) -> dict[str, Any]:
    return {
        "name": row.name,
        "entry_type": row.entry_type,
        "runs": row.runs,
        "duration": _metric_payload(row.duration),
        "count": _metric_payload(row.count),
    }


def build_reassure_entries_payload(
    *, import_id: int, entries: Sequence[ReassureEntryRow]
) -> dict[str, Any]:
    """Builds the stable `--json` per-import entries payload for `reassure
    entries <import-id>`. `entries` is already the full `Store.
    reassure_entries(import_id)` result — this builder only shapes, it
    never filters, sorts, or calls the store itself. `import_id` is the
    already-validated caller argument (the CLI checks
    `Store.reassure_import_exists` BEFORE calling this builder — an empty
    `entries` list here always means a real import with zero entries,
    never an unknown import)."""

    return {
        "schema_version": SCHEMA_VERSION,
        "import_id": import_id,
        "entries": [_entry_payload(row) for row in entries],
    }
