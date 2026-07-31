"""Doc-sync anti-drift guarantee for the README's `[PERF]` instrumentation
snippet (markers-command design "Testing" — "README doc-sync =
render_snippet('ts')==README fenced block via import (NO subprocess)").

`render_snippet('ts')` in `perf.cli.commands.markers` is the single source
of truth for the copy-paste emitter text. The README embeds that EXACT
output inline (spec: no "this example may be outdated" label — the
guarantee this test enforces stands in for one). If the snippet ever
changes and the README isn't regenerated to match, this test goes RED.

Extraction is anchored on a STABLE pair of HTML-comment markers around the
fenced ```ts block (`<!-- markers-snippet-ts:start -->` /
`:end`) — not a fragile line number or nearby prose — so unrelated README
edits can't silently break the anchor.
"""

from __future__ import annotations

import re
from pathlib import Path

from perf.cli.commands.markers import render_snippet

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README_PATH = _REPO_ROOT / "README.md"

_START_MARKER = "<!-- markers-snippet-ts:start -->"
_END_MARKER = "<!-- markers-snippet-ts:end -->"

_BLOCK_RE = re.compile(
    re.escape(_START_MARKER) + r"\s*```ts\n(.*?)```\s*" + re.escape(_END_MARKER),
    re.DOTALL,
)


def _extract_readme_ts_snippet() -> str:
    text = _README_PATH.read_text(encoding="utf-8")
    match = _BLOCK_RE.search(text)
    assert match is not None, (
        f"README.md must contain a ```ts block wrapped in {_START_MARKER} / {_END_MARKER} markers"
    )
    return match.group(1)


def test_readme_ts_snippet_matches_render_snippet_exactly():
    readme_snippet = _extract_readme_ts_snippet()
    assert readme_snippet == render_snippet("ts")
