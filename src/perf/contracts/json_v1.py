"""`--json` machine contract for `perf run`'s confirmation output (SKILL
rule 6: "the machine contract is `--json` (carries `schema_version`); the
pretty view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract
test MUST fail on any `--json` shape change without a `schema_version`
bump.").

`schema_version=1`. Stable, versioned, LOSSLESS: every marker value and
every per-iteration Flashlight aggregate is included verbatim (grouped,
never averaged away) so a future `compare` — or any external consumer —
can recompute percentiles/medians from the raw numbers. Contains NO
secrets: `env`/`PASSWORD` (or anything forwarded to the driver) is never
threaded into this module or its payload.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Sequence

from perf.application.run_flow import RunFlowResult
from perf.domain.model import Marker, SystemSample

__all__ = ["SCHEMA_VERSION", "build_run_payload"]

SCHEMA_VERSION = 1


def _measures_by_metric(markers: Sequence[Marker]) -> Mapping[str, Mapping[str, Any]]:
    """Group raw marker values by metric name — lossless (no averaging
    here; `n`/`values` let any consumer compute its own statistics)."""

    grouped: dict[str, dict[str, Any]] = {}
    for marker in markers:
        entry = grouped.setdefault(marker.name, {"unit": marker.unit, "values": []})
        entry["values"].append(marker.value)
    for entry in grouped.values():
        entry["n"] = len(entry["values"])
    return grouped


def _flashlight_samples(samples: Sequence[SystemSample]) -> list[dict[str, Any]]:
    """Verbatim per-iteration Flashlight aggregates — already the
    aggregate shape (design §3); no further averaging happens here."""

    return [asdict(sample) for sample in samples]


def build_run_payload(result: RunFlowResult) -> dict[str, Any]:
    """Builds the stable `--json` confirmation payload for a just-persisted
    `perf run`. Every field here is sourced from `RunFlowResult` — never
    from `request.env`/secrets, which this function does not even receive."""

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": result.run_id,
        "flow": result.flow_name,
        "device": result.device_key,
        "source": result.source,
        "commit": result.git_commit,
        "is_dev_bundle": result.is_dev_bundle,
        "mode": result.mode,
        "n": result.iterations,
        "partial_coverage": result.partial_coverage,
        "measures": _measures_by_metric(result.markers),
        "flashlight": _flashlight_samples(result.samples),
    }
