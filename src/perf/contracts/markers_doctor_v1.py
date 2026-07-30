"""`--json` machine contract for `perfvibe markers doctor` (SKILL rule 6:
"the machine contract is `--json` (carries `schema_version`); the pretty
view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract test
MUST fail on any `--json` shape change without a `schema_version` bump.").

`schema_version=1`. ONE coherent schema shape covers BOTH single-line
(`mode="line"`) and stdin/capture (`mode="stdin"`) modes — markers-command
spec "Doctor --json Payload": "MUST emit ONE coherent, schema_version-
carrying schema shape ... not two competing shapes." Mirrors
`contracts/init_v1.py`'s builder pattern: `build_doctor_payload` is PURE —
it accepts already-classified data (a `parsed` sequence of `Marker` plus
per-category counts/failure pairs) and shapes the dict; it does NOT call
`classify_line`/`parse()` itself (that bucketing wiring is a Phase 3
concern, per markers-command design.md's data-flow: "doctor fills these by
iterating classify_line over the buffer ... then calls build_doctor_payload").

`parse_failures` entries pair the raw offending line with the SAME reason
vocabulary `classify_line` already owns (`REASON_MALFORMED_TEXT` /
`REASON_INVALID_JSON` / `REASON_INVALID_VALUE` / `REASON_OVERSIZED` in
`perf.adapters.markers_adb_logcat`) — this module never redefines those
strings, it only threads them through (markers-command spec "Shared
Line-Classification Function": doctor must not duplicate classifier
vocabulary).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from perf.domain.model import Marker

__all__ = ["SCHEMA_VERSION", "build_doctor_payload"]

SCHEMA_VERSION = 1


def _parsed_payload(parsed: Sequence[Marker]) -> list[dict[str, Any]]:
    return [{"name": m.name, "value": m.value, "unit": m.unit} for m in parsed]


def _parse_failures_payload(parse_failures: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"line": line, "reason": reason} for line, reason in parse_failures]


def build_doctor_payload(
    *,
    mode: str,
    lines_scanned: int,
    parsed: Sequence[Marker],
    mark_start_without_end: int,
    perf_meta: int,
    parse_failures: Sequence[tuple[str, str]],
    ignored: int,
    coverage_ok: bool,
    diagnostic: str | None,
) -> dict[str, Any]:
    """Builds the stable `--json` payload for `markers doctor`. `mode` is
    `"line"|"stdin"` (markers-command spec "Doctor Input Mode Detection");
    `parsed` are the COMPLETED markers `classify_line` produced;
    `mark_start_without_end`/`perf_meta`/`ignored` are per-category counts;
    `parse_failures` pairs each failing raw line with its specific reason
    (spec "Diagnosis Categories": "the SPECIFIC reason MUST be reported on
    a PER-LINE basis"); `coverage_ok` is `bool(parsed) and not
    partial_coverage` (design.md); `diagnostic` surfaces
    `MarkerParseResult.diagnostic` verbatim, or `None` on full coverage."""

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "input_summary": {"lines_scanned": lines_scanned},
        "breakdown": {
            "parsed": _parsed_payload(parsed),
            "mark_start_without_end": mark_start_without_end,
            "perf_meta": perf_meta,
            "parse_failures": _parse_failures_payload(parse_failures),
            "ignored": ignored,
        },
        "coverage_ok": coverage_ok,
        "diagnostic": diagnostic,
    }
