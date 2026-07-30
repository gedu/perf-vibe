"""`--json` machine contract for `perfvibe markers snippet` (SKILL rule 6:
"the machine contract is `--json` (carries `schema_version`); the pretty
view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract test
MUST fail on any `--json` shape change without a `schema_version` bump.").

`schema_version=1`. Mirrors `contracts/init_v1.py`'s builder pattern: pure
`build_snippet_payload` with zero CLI/typer dependency, so a Phase 3
`markers snippet` command is not required to exist for this module to be
built and tested (markers-command design.md "Interfaces / Contracts":
`markers_snippet_v1 (schema_version=1): { "schema_version": 1, "lang":
"ts"|"js", "code": "<snippet>" }`).
"""

from __future__ import annotations

from typing import Any

__all__ = ["SCHEMA_VERSION", "build_snippet_payload"]

SCHEMA_VERSION = 1


def build_snippet_payload(*, lang: str, code: str) -> dict[str, Any]:
    """Builds the stable `--json` payload for `markers snippet`. `lang` is
    `"ts"|"js"` (markers-command spec "Snippet Language Selection"); `code`
    is the raw, paste-ready emitter text — no decoration that would break a
    copy-paste (spec "Snippet --json Payload": pretty mode prints the raw
    code only)."""

    return {
        "schema_version": SCHEMA_VERSION,
        "lang": lang,
        "code": code,
    }
