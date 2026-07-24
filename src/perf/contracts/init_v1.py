"""`--json` machine contract for `perfvibe init`'s scaffold/merge summary
(SKILL rule 6: "the machine contract is `--json` (carries `schema_version`);
the pretty view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract
test MUST fail on any `--json` shape change without a `schema_version`
bump.").

`schema_version=1`. Mirrors `contracts/compare_v1.py`'s shape but reports
scaffolding actions instead of a comparison verdict (design.md "`--json`
contract: New `contracts/init_v1.py`... its own lean, independently
contract-tested shape"). Pure — `build_init_payload` has zero CLI/typer
dependency; it is a plain function over primitives so Phase 1 needs no
`init` command or domain object to exist yet.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

__all__ = ["SCHEMA_VERSION", "build_init_payload"]

SCHEMA_VERSION = 1


def _flows_skipped_payload(
    flows_skipped: Iterable[tuple[str, str]],
) -> list[dict[str, str]]:
    return [{"name": name, "reason": reason} for name, reason in flows_skipped]


def build_init_payload(
    *,
    config_path: str,
    bundle_id: str | None,
    bundle_id_source: str,
    flows_added: Sequence[str],
    flows_skipped: Sequence[tuple[str, str]],
    flows_total: int,
    appid_conflict: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Builds the stable `--json` summary payload for a `perfvibe init`
    invocation. `bundle_id_source` is one of `"detected"|"flag"|"prompt"|
    "none"` (design.md's `init_v1` shape); `flows_skipped` pairs a flow
    name with why it was skipped (e.g. `"exists"` on a collision without
    `--force`); `appid_conflict` lists the conflicting concrete `appId`
    values, or `None` when reconciliation found no mismatch."""

    return {
        "schema_version": SCHEMA_VERSION,
        "config_path": config_path,
        "bundle_id": bundle_id,
        "bundle_id_source": bundle_id_source,
        "flows_added": list(flows_added),
        "flows_skipped": _flows_skipped_payload(flows_skipped),
        "flows_total": flows_total,
        "appid_conflict": list(appid_conflict) if appid_conflict is not None else None,
    }
