"""`--json` reporter — the ONLY machine-parseable output path (SKILL rule
6). Renders the stable `contracts/*_v1` payload with ONE normalization the
contract itself requires: the stream must be RFC-8259-valid, and non-finite
floats (`inf` from `regression.classify`'s baseline-0 case, any stray
`nan`) have no JSON representation — they serialize as `null`. Python's
`json.dumps` default would emit the invalid literals `NaN`/`Infinity`,
which `jq`/`JSON.parse` reject; `allow_nan=False` is kept as a hard
backstop so any future non-finite leak that dodges the sanitizer raises
loudly instead of emitting garbage."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["render_json"]


def _sanitize(value: Any) -> Any:
    # bool is an int subclass and never non-finite — check float only, so
    # `true`/`false`/ints pass through untouched.
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_sanitize(item) for item in value]
    return value


def render_json(payload: Mapping[str, Any]) -> str:
    # `sort_keys=True` keeps byte-for-byte output stable across runs with
    # the same payload — useful for scripts/snapshots, never required by
    # the contract itself (the contract test asserts shape, not key order).
    return json.dumps(_sanitize(payload), sort_keys=True, allow_nan=False)
