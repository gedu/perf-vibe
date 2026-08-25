"""Terminal-rendering primitives shared by every `perfvibe` pretty renderer —
the ANSI codes, glyphs, and tiny pure formatters that the `compare`,
`history`, `budget-check`, `run` and flow-picker views all draw with.

WHY THIS MODULE EXISTS. `budget_check_pretty` used to re-implement the
sparkline and the arrow/pct formatter rather than importing them, and said so
in a docstring: the point was that one renderer must not depend on another,
because importing `compare_pretty` would have coupled budget-check's view to
a frozen module it does not own. That concern was real, and a shared module
answers it better than either duplication or a cross-renderer import: no
renderer depends on another renderer, and each depends instead on a primitive
that belongs to NONE of them. The vocabulary lives here precisely so it is
nobody's private property.

WHAT BELONGS HERE. Only vocabulary with more than one real caller today: a
code that paints, a glyph that means something, or a pure value->string
formatter. What stays with its renderer is anything that encodes ONE view's
layout decisions — budget-check's column spec and its `_table_line`, and its
detail chart (`_y_ticks`/`_render_chart`) — because those read that view's own
constants and have exactly one caller. Generalizing them now would mean
inventing a table/chart engine for a single user (`python-architecture` rule
3, rule of three).

HAND-ROLLED, NOT `rich`: every helper takes an explicit `color: bool` and
emits ZERO ANSI escapes when it is false, so plain-text output stays
byte-identical for the golden tests (`perf-cli-output` output contract).
"""

from __future__ import annotations

from collections.abc import Sequence

from perf.domain import regression
from perf.domain.model import Verdict

__all__ = [
    "ARROW_DOWN",
    "ARROW_FLAT",
    "ARROW_NONE",
    "ARROW_UP",
    "BOLD",
    "BOLD_GREEN",
    "BOLD_RED",
    "DIM",
    "GLYPH_NEUTRAL",
    "GLYPH_OFFENDER",
    "GLYPH_OK",
    "GREEN",
    "RESET",
    "REVERSE",
    "SPARK_CHARS",
    "YELLOW",
    "arrow_and_pct",
    "format_value",
    "sparkline",
    "style",
]

# ===== ANSI codes =====
# Named by exactly what they paint, so a caller cannot mistake bold green for
# plain green: both are in use and they are NOT interchangeable (`run`'s
# confirmation tick is plain, `budget-check`'s GATE PASSED banner is bold).
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
REVERSE = "\x1b[7m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BOLD_RED = "\x1b[1;31m"
BOLD_GREEN = "\x1b[1;32m"

# ===== Glyphs =====
# Stdlib Unicode block characters, low -> high (design "UX": "▁▂▃▅▇").
SPARK_CHARS = "▁▂▃▄▅▆▇█"

ARROW_UP = "↑"
ARROW_DOWN = "↓"
ARROW_FLAT = "→"
ARROW_NONE = "-"

# Status glyphs. Emphasis must never depend on color alone, so these ride
# ALONGSIDE the status word rather than replacing it.
GLYPH_OFFENDER = "✗"
GLYPH_OK = "✓"
GLYPH_NEUTRAL = "·"


def style(text: str, *, color: bool, code: str) -> str:
    """Wrap `text` in `code` and reset, or return it untouched when `color` is
    false — the single place the whole CLI decides whether an escape is
    emitted at all."""

    return f"{code}{text}{RESET}" if color else text


def sparkline(series: Sequence[float]) -> str:
    """Normalizes `series` to its own min/max and maps each point to one of
    the 8 block-char levels. Handles empty, single-point, and `max == min`
    (zero variance) without a divide-by-zero (spec 'Pretty-Output UX'
    sparkline edges; design risk #3 requires this same guard in every view
    that draws a series)."""

    if not series:
        return ""
    if len(series) == 1:
        return SPARK_CHARS[0]

    lo, hi = min(series), max(series)
    span = hi - lo
    if span == 0:
        # Zero-variance series — render the flat middle level for every
        # point rather than dividing by zero.
        flat = SPARK_CHARS[len(SPARK_CHARS) // 2]
        return flat * len(series)

    top_index = len(SPARK_CHARS) - 1
    return "".join(SPARK_CHARS[round((value - lo) / span * top_index)] for value in series)


def format_value(value: float | None, unit: str | None = None) -> str:
    """One decimal place, or `-` for a missing value. `unit=None` omits the
    unit ENTIRELY (`"120.0"` — the `compare`/`history` shape); any string,
    including `""`, is appended after a space (`"120.0 ms"` — the
    `budget-check` shape). The `is None` test rather than a falsy test is
    what keeps an empty unit rendering its trailing space exactly as
    budget-check always has."""

    if value is None:
        return "-"
    if unit is None:
        return f"{value:.1f}"
    return f"{value:.1f} {unit}"


def arrow_and_pct(verdict: Verdict) -> tuple[str, str]:
    """The direction glyph and signed percentage for `verdict`'s delta.
    Insufficient data renders as a flat `-`/`-` pair rather than a fabricated
    0% move."""

    if verdict.status == regression.STATUS_INSUFFICIENT_DATA:
        return ARROW_NONE, "-"
    delta_pct = verdict.delta_pct
    arrow = ARROW_UP if delta_pct > 0 else ARROW_DOWN if delta_pct < 0 else ARROW_FLAT
    sign = "+" if delta_pct >= 0 else ""
    return arrow, f"{sign}{delta_pct:.1f}%"
