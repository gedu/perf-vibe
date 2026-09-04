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
code that paints, a glyph that means something, a pure value->string
formatter, the table layout that `budget-check` and `compare` now BOTH draw
with, and the y-axis chart that `budget-check` and `history` now BOTH draw
with. What stays with its renderer is anything that encodes ONE view's layout
decisions — each view's own column spec, and history's own per-run delta —
because those read that view's own constants and have exactly one caller.

WHY THE CHART MOVED. This docstring used to name `_y_ticks`/`_render_chart`
as the example of what must NOT be promoted, on the grounds that generalizing
them would mean "inventing a chart engine for a single user"
(`python-architecture` rule 3, rule of three). That reasoning was right and it
expired: `history` is a second real caller that needs the SAME chart, and the
alternative was a second implementation of tick arithmetic. The promotion is
deliberately partial — `chart_lines` returns UNPREFIXED lines and knows
nothing about a box rail, an empty-series message, or a HEAD marker, because
those are each one view's decision. Byte-identical `budget_check_*` goldens
are the proof the move changed no output.

WHY `table_line` IS HERE NOW AND WAS NOT BEFORE. It shipped private to
`budget_check_pretty` on purpose: it read that view's `_SUMMARY_COLUMNS` from
module scope and had exactly ONE caller, so sharing it would have meant
building a table engine for a single user. `compare` is the second real
caller with the same shape, so the spec became a PARAMETER and the layout
moved here. The two views still own their own column specs — only the
mechanics are shared.

HAND-ROLLED, NOT `rich`: every helper takes an explicit `color: bool` and
emits ZERO ANSI escapes when it is false, so plain-text output stays
byte-identical for the golden tests (`perf-cli-output` output contract).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import NamedTuple

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
    "CHART_COL_W",
    "CHART_PREFIX_W",
    "CHART_ROWS",
    "DIM",
    "GLYPH_NEUTRAL",
    "GLYPH_OFFENDER",
    "GLYPH_OK",
    "GREEN",
    "RESET",
    "REVERSE",
    "SPARK_CHARS",
    "TABLE_GAP",
    "YELLOW",
    "Cell",
    "ColumnSpec",
    "arrow_and_pct",
    "chart_lines",
    "device_label",
    "format_value",
    "header_line",
    "sanitize_untrusted_text",
    "sparkline",
    "style",
    "table_line",
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
    emitted at all.

    An EMPTY `code` is also returned untouched, even with `color=True`. A
    caller that picks its code conditionally (`DIM if dimmed else ""`) would
    otherwise emit a bare `RESET` with nothing opening it, which closes
    whatever span the terminal happened to be in. `table_line` already carried
    a local `if code else text` guard for exactly this; the trap belongs here,
    where every caller gets it."""

    if not color or not code:
        return text
    return f"{code}{text}{RESET}"


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


# ===== Table layout =====
# `(title, width, alignment)`. Width 0 marks a flexible column that is emitted
# unpadded — only ever useful as the LAST one.
ColumnSpec = tuple[str, int, str]

TABLE_GAP = 2


class Cell(NamedTuple):
    """One table cell: its plain text plus the ANSI code that paints it.

    A cell exists so a renderer can paint PART of a row without wrecking the
    column arithmetic: `table_line` measures `text` and pads OUTSIDE the
    escape sequences, which a caller cannot do by pre-styling a string
    (`len("\\x1b[1;31m✗\\x1b[0m") == 10`, so the column would come out 9
    characters short). An empty `code` — the default, and what a bare `str`
    cell becomes — never emits an escape at all, whatever `color` says.
    """

    text: str
    code: str = ""


def table_line(
    cells: Sequence[str | Cell],
    columns: Sequence[ColumnSpec],
    *,
    color: bool = False,
    gap: int = TABLE_GAP,
) -> str:
    """Lays out ONE table line from `columns`. The header and every data row
    go through here, which is what keeps a column and the header that labels
    it from ever drifting apart — `budget_check_pretty`'s first cut kept two
    hand-tuned f-strings, trusted them to agree by eye, and they drifted 2-8
    columns apart while the golden froze the misalignment without complaint.
    A golden proves output is STABLE, never that it is right; deriving both
    lines from one spec makes that drift impossible by construction.

    Padding is computed from `len(cell.text)`, so it is measured on the plain
    text and applied OUTSIDE any escape sequence. Trailing whitespace is
    stripped, so a right-aligned final column ends the line exactly at the
    table's width.
    """

    parts: list[str] = []
    for (_, width, align), cell in zip(columns, cells, strict=True):
        text, code = (cell, "") if isinstance(cell, str) else cell
        painted = style(text, color=color, code=code) if code else text
        if not width:
            parts.append(painted)
            continue
        pad = " " * max(0, width - len(text))
        parts.append(painted + pad if align == "<" else pad + painted)
    return (" " * gap).join(parts).rstrip()


def header_line(columns: Sequence[ColumnSpec], *, gap: int = TABLE_GAP) -> str:
    """The column titles laid out by their OWN spec — never hand-spaced, and
    never colored (a header is structure, not a verdict)."""

    return table_line([title for title, _, _ in columns], columns, gap=gap)


# ===== Y-axis chart =====
# `budget-check`'s detail chart, promoted when `history` became a second real
# caller. A sparkline says "it went up"; a chart with labelled ticks says HOW
# FAR, which is the whole reason both views draw one.
CHART_ROWS = 5
CHART_COL_W = 8
# The `"{value:>7.1f} ┤ "` gutter: 7 for the tick + 3 for " ┤ ". Callers indent
# the axis and label lines by exactly this much so they line up under the bars.
CHART_PREFIX_W = 10

CHART_BAR = "██"

# A tick is a float computed from the data, so a value that IS the tick must
# still clear it — `100.0 >= 100.00000000000001` is how an exact top-of-chart
# point silently loses its bar.
_CHART_EPSILON = 1e-9


def _y_ticks(values: Sequence[float], *, rows: int) -> list[float]:
    """`rows` evenly spaced tick values from the series max DOWN to its min.
    A zero-variance series collapses to its single value rather than dividing
    by zero — the same guard `sparkline` needs, for the same reason."""

    lo, hi = min(values), max(values)
    if hi == lo:
        return [lo]
    return [hi - (i / (rows - 1)) * (hi - lo) for i in range(rows)]


def chart_lines(
    values: Sequence[float],
    labels: Sequence[str],
    *,
    rows: int = CHART_ROWS,
    col_w: int = CHART_COL_W,
) -> list[str]:
    """A column chart of `values` with labelled y-axis ticks and `labels`
    along the x axis, as UNPREFIXED lines: the tick rows, the `└───` axis, then
    the x labels.

    Deliberately knows nothing about a box rail, an empty-series message, or a
    HEAD marker — each of those is one view's decision, so each caller prepends
    its own rail and appends its own annotations. Returns an EMPTY list for an
    empty series so a caller can substitute its own wording instead of being
    handed a chart of nothing.

    `labels` must be the same length as `values`: the whole point is that a bar
    and the label under it describe the same run, and a zip that silently
    truncated would misattribute every bar after the mismatch.
    """

    if not values:
        return []
    if len(labels) != len(values):
        raise ValueError(
            f"chart_lines needs one label per value, got {len(labels)} labels "
            f"for {len(values)} values"
        )

    lines = [
        (
            f"{tick:>7.1f} ┤ "
            + "".join(
                f"{CHART_BAR if value >= tick - _CHART_EPSILON else '':<{col_w}}"
                for value in values
            )
        ).rstrip()
        for tick in _y_ticks(values, rows=rows)
    ]
    gutter = " " * CHART_PREFIX_W
    lines.append(gutter + "└" + "─" * (col_w * len(values)))
    lines.append((gutter + "".join(f"{label:<{col_w}}" for label in labels)).rstrip())
    return lines


def sanitize_untrusted_text(text: str) -> str:
    """Replaces every Unicode CONTROL code point (`unicodedata.category(ch)
    == "Cc"`: C0 `0x00`-`0x1F`, `DEL` `0x7F`, and the C1 range `0x80`-`0x9F`,
    which some terminals treat as 8-bit escape/CSI introducers just like
    the more familiar `ESC` `0x1B`) with `U+FFFD` (REPLACEMENT CHARACTER)
    (re-verification finding W-6).

    WHY THIS EXISTS. `reassure-read` is the first code in this CLI to
    render text it did not generate itself onto a real terminal: a
    reassure entry's `name` (and a `.perf` header's `branch`/`commit_hash`)
    come straight from a third-party-generated `.perf` file, fully
    attacker-controlled. A crafted name carrying `ESC[31m`/`ESC[0m` (an
    SGR color override) or a cursor-movement/erase-line sequence can
    repaint or overwrite ALREADY-PRINTED output — including a `REGRESSION`
    row or the D5 sentence — the moment it reaches a real TTY. Invisible to
    a capture-based test, because `click.echo`'s ANSI stripping (and every
    test in this suite capturing rather than allocating a pty) only
    triggers on a NON-terminal stream.

    SCOPE, DELIBERATELY NARROW. Only the `Cc` category is touched — real
    script text (Cyrillic, CJK, Arabic, emoji, combining marks) is NOT in
    that category and passes through completely untouched; reassure test
    names are human-written English in practice, but nothing here assumes
    ASCII. This is a RENDERING-ONLY concern: the `--json` path is never
    routed through this function — `json.dumps` already escapes every
    control byte as `\\uXXXX`, and mangling raw payload bytes here would
    corrupt data an agent needs to match against its own records."""

    return "".join("�" if unicodedata.category(ch) == "Cc" else ch for ch in text)


def device_label(device_key: str) -> str:
    """The human half of a `model|os|kind` device key. A reader recognizes
    `Pixel 8 Pro`, not the pipe-delimited key, and the OS/kind halves are not
    what distinguishes one view from another on a dev's machine. Degrades the
    same way `run` does: a key derived with no device attached carries the
    literal `unknown`, which reads as nothing at all in a header, so it becomes
    `unknown device` instead (matching `budget_check_pretty`'s
    `rc.model or "unknown device"`).

    Shared rather than duplicated because `compare` and `history` both draw a
    box header off the SAME `device_key` string. `budget-check` still does its
    own thing: it holds a `RunContext`, so it reads `rc.model` directly and has
    no key to parse."""

    model = device_key.split("|")[0].strip()
    if not model or model == "unknown":
        return "unknown device"
    return model
