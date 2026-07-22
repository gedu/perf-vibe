"""`--json` reporter — the ONLY machine-parseable output path (SKILL rule
6). Renders the stable `contracts/json_v1` payload verbatim; performs no
formatting decisions of its own so the contract test's guarantees hold
all the way to stdout."""

from __future__ import annotations

import json
from typing import Any, Mapping

__all__ = ["render_json"]


def render_json(payload: Mapping[str, Any]) -> str:
    # `sort_keys=True` keeps byte-for-byte output stable across runs with
    # the same payload — useful for scripts/snapshots, never required by
    # the contract itself (the contract test asserts shape, not key order).
    return json.dumps(payload, sort_keys=True)
