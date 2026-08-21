"""`--json` machine contract for `perfvibe reassure-import` (SKILL rule 6:
"the machine contract is `--json`"; SKILL rule 8: "A contract test MUST
fail on any `--json` shape change without a `schema_version` bump.").

`schema_version=2`. FLAT, exactly TEN top-level keys, no nested objects
and no arrays: `schema_version`, `path`, `content_hash`, `kind`,
`already_imported`, `entries_imported`, `entries_skipped`,
`duration_samples_imported`, `count_samples_imported`,
`entries_with_render_issues`.

`kind` is the ninth key, joining the eight-key draft the delta spec
originally pinned: PR4a (`0006_add_reassure_import_kind.sql`) added the
`reassure_import.kind` column, but nothing wrote it yet — PR4b is the first
slice that derives/persists a real `kind` value, so only now does the
payload have something non-default to report. A contract may only pin a
key the payload actually needs to carry; `kind` could not have been frozen
before its write path existed.

`entries_with_render_issues` is the TENTH key.
`0007_add_reassure_entry_issues.sql` is the first slice that parses and
persists reassure's `issues` diagnostics, so before it there was nothing here
to report. It counts imported entries whose
`issues.initialUpdateCount` is GREATER THAN ZERO — entries where reassure
found an EXTRA RENDER ON MOUNT, which is reassure's own actionable finding
and the reason `issues` is persisted at all. Unlike `zero_entries` (refused
below), this count is NOT derivable from any other key in the payload, which
is what earns it a place.

`SCHEMA_VERSION` is bumped to `2` for it. The project rule is unqualified —
`perf-cli-standards` rule 8: "A contract test MUST fail on any `--json` shape
change without a `schema_version` bump" — and adding a key IS a shape change.
`init_v1.py` already sits at `SCHEMA_VERSION = 2`, so bumping while the module
keeps its `_v1` family name is the established house shape, not a novelty.

An earlier revision of this module argued the version could stay at `1`,
reasoning that a key added alongside the data behind it breaks no consumer,
and cited `kind` joining as the ninth key at version 1 as precedent. That
reasoning is defensible in the abstract, but `kind` was an oversight rather
than a decision, so repeating it would be inheriting a mistake rather than
following a rule. A cheap bump beats a rule bent to fit one change: if the
distinction is worth having, it belongs in the skill that owns the rule for
every contract, not in this module or in one capability's spec.

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

SCHEMA_VERSION = 2


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
    entries_with_render_issues: int,
) -> dict[str, Any]:
    """Builds the stable `--json` payload for `reassure-import`. `path` is
    the resolved input path (the payload key intentionally differs from
    the DDL's `source_path` column — see the design's "Contract" section).
    `already_imported` is `True` exactly when the store returned `None`
    (a byte-identical re-import); in that case every `*_imported` counter
    is `0` by construction. `entries_skipped` is the COUNT of skipped
    lines only — the per-line `(number, reason)` detail is stderr-only
    (`emit_warning`), never part of this machine contract.
    `entries_with_render_issues` counts imported entries whose
    `issues.initialUpdateCount` is greater than zero; entries whose `issues`
    was absent are NOT counted, since an absent diagnostic is not a finding."""

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
        "entries_with_render_issues": entries_with_render_issues,
    }
