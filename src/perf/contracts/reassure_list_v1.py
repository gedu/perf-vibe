"""`--json` machine contract for `perfvibe reassure list` (SKILL rule 6:
"the machine contract is `--json` (carries `schema_version`); the pretty
view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract test
MUST fail on any `--json` shape change without a `schema_version` bump.").

`schema_version=1`. Top-level FLAT dict with exactly TWO keys —
`schema_version` and `imports`, a list of per-import dicts, each one flat —
mirroring `history_v1`'s `{schema_version, flow, device, mode, runs: [...]}`
shape rather than `reassure_import_v1`'s single-row shape: `list` reports
MANY imports, `import` confirms exactly ONE.

Each per-import dict carries exactly what spec "reassure list — Import
Roster" requires: "an import identifier, `created_date`/`imported_at`,
`branch`, `commit_hash` (as a label), and its entry count" — plus
`source_path`, mirroring `reassure_import_v1`'s `path` key (which file the
import came from).

Deliberately absent from each per-import dict, by the SAME
no-second-source-of-truth rule `reassure_import_v1` already applies (its
module docstring: "one count cannot describe two independently-sized
series" / "a pure derived AND of two fields already present"):
- `ordering_key` — mechanically `'created_date' if created_date is not
  None else 'imported_at'`. A consumer already holding `created_date` in
  this SAME dict can derive this in one line; persisting it risks the
  exact drift the rule exists to prevent (design D2). `ReassureImportRow`
  (the read model) still carries it — `reassure_list_pretty.py` reads it
  directly off the row, never off this payload — because the PRETTY view
  is a separate rendering path over the raw rows, not a consumer of this
  `--json` contract.
- `ordered_at` — mechanically `created_date or imported_at`
  (`COALESCE`, resolved in SQL for the STORE's own `ORDER BY`, but
  trivially re-derivable by any JSON consumer from the two fields already
  here). Same rule, same reasoning.
- `kind` — design A5: `reassure_import.kind` is NEVER SELECTed by any
  query in this capability, so no read model this change ships — including
  this one — ever carries it.

Mirrors `contracts/reassure_import_v1.py`'s and `contracts/history_v1.py`'s
pure-builder pattern: `build_reassure_list_payload` accepts an
already-fetched `Sequence[ReassureImportRow]` and shapes the dict — it
never calls `Store.reassure_imports` itself, and never sorts or filters
(D2 ordering and `--limit` are the STORE's job)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from perf.domain.model import ReassureImportRow

__all__ = ["SCHEMA_VERSION", "build_reassure_list_payload"]

SCHEMA_VERSION = 1


def _import_payload(row: ReassureImportRow) -> dict[str, Any]:
    return {
        "import_id": row.import_id,
        "imported_at": row.imported_at,
        "created_date": row.created_date,
        "branch": row.branch,
        "commit_hash": row.commit_hash,
        "source_path": row.source_path,
        "entry_count": row.entry_count,
    }


def build_reassure_list_payload(*, imports: Sequence[ReassureImportRow]) -> dict[str, Any]:
    """Builds the stable `--json` roster payload for `reassure list`.
    `imports` is already ordered per D2 (most recent first) and already
    limited by the caller — this builder only shapes, it never filters,
    sorts, or calls the store itself."""

    return {
        "schema_version": SCHEMA_VERSION,
        "imports": [_import_payload(row) for row in imports],
    }
