"""`--json` machine contract for `perfvibe reassure-import` (SKILL rule 6:
"the machine contract is `--json`"; SKILL rule 8: "A contract test MUST
fail on any `--json` shape change without a `schema_version` bump.").

`schema_version=1`. FLAT, exactly NINE top-level keys, no nested objects
and no arrays: `schema_version`, `path`, `content_hash`, `kind`,
`already_imported`, `entries_imported`, `entries_skipped`,
`duration_samples_imported`, `count_samples_imported`.

`kind` is the ninth key, joining the eight-key draft the delta spec
originally pinned: PR4a (`0006_add_reassure_import_kind.sql`) added the
`reassure_import.kind` column, but nothing wrote it yet — PR4b is the first
slice that derives/persists a real `kind` value, so only now does the
payload have something non-default to report. A contract may only pin a
key the payload actually needs to carry; `kind` could not have been frozen
before its write path existed.

Deliberately absent, by design (see the `reassure-ingest` spec requirement
"reassure_import_v1 --json Contract"):
- `samples_imported` — one count cannot describe two independently-sized
  series (`durations[]`/`counts[]` are NOT index-aligned); the payload
  reports each series separately via `duration_samples_imported` and
  `count_samples_imported`, neither forced to match the other.
- `zero_entries` — a pure derived AND of `entries_imported == 0` and
  `already_imported == false`. Persisting it would repeat the exact
  second-source-of-truth problem this change already rejects for
  `meanDuration`/`stdevDuration`/`meanCount`/`stdevCount`: the consumer
  derives it from the two fields already present.

Mirrors `contracts/markers_doctor_v1.py`'s pure-builder pattern:
`build_reassure_import_payload` accepts already-computed scalars and shapes
the dict — it never calls the parser or the store itself.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SCHEMA_VERSION", "build_reassure_import_payload"]

SCHEMA_VERSION = 1


def build_reassure_import_payload(
    *,
    path: str,
    content_hash: str,
    kind: str,
    already_imported: bool,
    entries_imported: int,
    entries_skipped: int,
    duration_samples_imported: int,
    count_samples_imported: int,
) -> dict[str, Any]:
    """Builds the stable `--json` payload for `reassure-import`. `path` is
    the resolved input path (the payload key intentionally differs from
    the DDL's `source_path` column — see the design's "Contract" section).
    `already_imported` is `True` exactly when the store returned `None`
    (a byte-identical re-import); in that case every `*_imported` counter
    is `0` by construction. `entries_skipped` is the COUNT of skipped
    lines only — the per-line `(number, reason)` detail is stderr-only
    (`emit_warning`), never part of this machine contract."""

    return {
        "schema_version": SCHEMA_VERSION,
        "path": path,
        "content_hash": content_hash,
        "kind": kind,
        "already_imported": already_imported,
        "entries_imported": entries_imported,
        "entries_skipped": entries_skipped,
        "duration_samples_imported": duration_samples_imported,
        "count_samples_imported": count_samples_imported,
    }
