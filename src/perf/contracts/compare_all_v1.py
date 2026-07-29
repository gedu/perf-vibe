"""`--json` machine contract for `perfvibe compare` over MULTIPLE flows
(`compare <flow> <flow> …` or `--all`) — SKILL rule 6/8. Single-flow
`--json` keeps emitting the plain `compare_v1` payload UNCHANGED; this
envelope is used ONLY for the 2+/`--all` case.

`schema_version=1`. The envelope is a thin, ordered wrapper that EMBEDS the
per-flow `compare_v1` payload verbatim (never re-serializing its fields), so
the two contracts stay decoupled but consistent: a flow WITH history becomes
`{"flow": <name>, "result": <compare_v1 payload>}`; a flow that was skipped
for having NO recorded history becomes `{"flow": <name>, "error":
"no-history"}`. Order mirrors the caller's selection order. Contains NO
secrets — it only ever receives flow names and `CompareResult`s.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from perf.contracts.compare_v1 import build_compare_payload
from perf.domain.model import CompareResult

__all__ = ["SCHEMA_VERSION", "build_compare_all_payload"]

SCHEMA_VERSION = 1

ERROR_NO_HISTORY = "no-history"


def _flow_entry(flow_name: str, result: CompareResult | None) -> dict[str, Any]:
    if result is None:
        return {"flow": flow_name, "error": ERROR_NO_HISTORY}
    return {"flow": flow_name, "result": build_compare_payload(result)}


def build_compare_all_payload(
    results: Sequence[tuple[str, CompareResult | None]],
) -> dict[str, Any]:
    """Build the multi-flow `--json` envelope from an ORDERED sequence of
    `(flow_name, result_or_None)` pairs. A `None` result marks a flow with no
    recorded history (skipped, surfaced as an `error` entry rather than
    dropped) so the machine consumer sees every requested flow accounted
    for."""

    return {
        "schema_version": SCHEMA_VERSION,
        "flows": [_flow_entry(flow_name, result) for flow_name, result in results],
    }
