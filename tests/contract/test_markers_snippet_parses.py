"""Anti-drift contract (markers-command spec "Text-Form Emitter Contract",
scenario "Emitted line parses cleanly"): the ONE representative marker line
`markers snippet`'s generated code would emit (`emitted_sample()`) MUST
parse cleanly through the REAL `AdbLogcatMarkerSource.parse()` — the SAME
parser `perf run` uses. `emitted_sample()` and `render_snippet()` both
import the SAME `PERF_TAG` constant the parser consumes (spec "Shared
PERF_TAG Constant"), so neither can silently drift from the other.
markers-command Phase 3 task 3.5.

W-1 fix (verify-report Phase 3 finding): `emitted_sample()` used to be an
INDEPENDENTLY hand-authored string sharing only `PERF_TAG` with the
snippet's actual `console.log` line — a drift in the line SHAPE (the `: `
separator, the `ms` unit) would have passed this file green while breaking
every pasted snippet. `test_the_snippets_actual_emitted_line_parses_cleanly`
below closes that gap: it extracts the REAL `console.log` template literal
from a RENDERED snippet, substitutes it exactly as the JS runtime would,
and feeds THAT (not a hand-written duplicate) through the real parser.
"""

from __future__ import annotations

import re

import pytest

from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource
from perf.cli.commands.markers import emitted_sample, render_snippet
from perf.domain.model import PERF_TAG, Marker

_CONSOLE_LOG_RE = re.compile(r"console\.log\(`([^`]+)`\);")


def _extract_runtime_line(code: str, *, name: str, duration: str) -> str:
    """Extracts the ACTUAL `console.log` template-literal body a RENDERED
    snippet contains and substitutes it exactly as the JS runtime would at
    `markEnd()` time — proving the line the snippet REALLY emits (not an
    independently hand-authored duplicate) parses cleanly."""

    match = _CONSOLE_LOG_RE.search(code)
    assert match is not None, "rendered snippet must contain one console.log(`...`) call"
    return match.group(1).replace("${name}", name).replace("${measureEntry.duration}", duration)


def test_emitted_sample_parses_into_exactly_one_expected_marker():
    result = AdbLogcatMarkerSource().parse([emitted_sample()], iterations=1)
    assert result.markers == (Marker(name="example", value=123.0, unit="ms"),)
    assert result.partial_coverage is False


def test_emitted_sample_starts_with_the_shared_perf_tag():
    assert emitted_sample().startswith(PERF_TAG)


def test_rendered_snippets_embed_the_shared_perf_tag_for_both_langs():
    assert PERF_TAG in render_snippet("ts")
    assert PERF_TAG in render_snippet("js")


@pytest.mark.parametrize("lang", ["ts", "js"])
def test_the_snippets_actual_emitted_line_parses_cleanly(lang):
    """The REAL anti-drift proof (W-1): extracts the line the SNIPPET's OWN
    `console.log` call would emit at runtime — never a hand-authored
    duplicate — and feeds it through the REAL parser."""

    code = render_snippet(lang)
    line = _extract_runtime_line(code, name="checkout", duration="450")
    result = AdbLogcatMarkerSource().parse([line], iterations=1)
    assert result.markers == (Marker(name="checkout", value=450.0, unit="ms"),)
    assert result.partial_coverage is False


def test_snippet_console_log_uses_the_same_line_shape_emitted_sample_uses():
    """Structural fallback for W-1: even without extraction, the snippet
    body must embed the EXACT SAME line shape `emitted_sample()` uses —
    a drift in either one fails this."""

    shape = (
        emitted_sample().replace("example", "${name}").replace("123", "${measureEntry.duration}")
    )
    assert shape in render_snippet("ts")
    assert shape in render_snippet("js")
