"""Pretty verdict reporter for `perf compare` — human-readable, LOSSY (it
summarizes; it must NEVER be parsed — SKILL rule 6). Color/TTY-aware via the
caller-resolved `color` flag (golden tests force it off; the CLI resolves it
via the shared `OutputContext`, mirroring `cli/output/pretty.py`'s
`render_confirmation`).

LAYOUT: the SAME table `budget-check` draws — a labelled header row over a
rule, laid out by `primitives.table_line` from this view's own column spec —
inside the same OPEN-RIGHT box (`┌─` top, a `│` left rail, `└─` bottom, and
NEVER a right border). The previous shape had no headers at all, so
`1310.0 vs 812.0` gave a reader no way to tell latest from baseline; naming
the columns is the whole point of the change.

WHY A `min→max` COLUMN. A sparkline is normalized to its own min/max, so
`▁▁▁▁█` says "it went up at the end" and NOTHING about how far: 100→110 and
812→1310 draw identically. The scale column is what makes the trend
readable. It carries the series' RANGE, rounded for scanability (the exact
numbers live in LATEST/BASELINE), and it never fabricates a range it does not
have — a one-point series prints the single value, and an empty one prints
`-`.

WHY ONLY THREE CELLS ARE PAINTED. The old renderer styled the ENTIRE row
bold-red on a regression, which made the numbers harder to read than leaving
them alone, and it had no green at all — an improvement looked exactly like a
flat result. Now the glyph, the Δ and the STATUS word carry the color (red
regression, green improvement, dim otherwise) and the numbers stay
unpainted. Emphasis still never depends on color alone: every row carries
BOTH a glyph (`✗`/`✓`/`·`) AND the status word, uppercased only for a
`regression`, so `color=False` output is just as legible and emits zero ANSI.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence

from perf.cli.output.primitives import (
    BOLD_GREEN,
    BOLD_RED,
    DIM,
    GLYPH_NEUTRAL,
    GLYPH_OFFENDER,
    GLYPH_OK,
    Cell,
    ColumnSpec,
    arrow_and_pct,
    format_value,
    header_line,
    sparkline,
    style,
    table_line,
)
from perf.domain import calibration, regression
from perf.domain.calibration import CalibrationReport
from perf.domain.model import CompareResult, Verdict

__all__ = ["render_compare"]

# This view OWNS its spec; only the layout mechanics are shared. It is
# `budget-check`'s spec plus a scale column, so a reader who knows one table
# knows the other. The one shape change: TREND must be FIXED-width here,
# because something follows it — as budget-check's flexible trailing column it
# never needed a width.

# Sized for the common series, not the longest possible one: a deeper history
# overflows it and pushes the scale column right, which is the SAME open-right
# tolerance budget-check already accepts for its trailing sparkline. Widening
# this to the theoretical maximum (`baseline_n` + the latest run) would strand
# a typical 4-5 point sparkline in a sea of padding, and every metric in one
# render shares the same run history — so an overflow shifts all rows together
# and they stay aligned with each other.
_TREND_W = 8
_COMPARE_COLUMNS: tuple[ColumnSpec, ...] = (
    ("", 1, "<"),  # status glyph — a real column, so the header cannot drift past it
    ("METRIC", 14, "<"),
    ("LATEST", 11, ">"),
    ("BASELINE", 11, ">"),
    ("Δ", 9, ">"),
    ("STATUS", 17, "<"),  # 17 fits `insufficient-data` exactly
    ("TREND", _TREND_W, "<"),
    ("min→max", 9, ">"),
)

# Derived, never hand-counted: the rule spans exactly the table it underlines.
_HEADER_LINE = header_line(_COMPARE_COLUMNS)
_RULE_WIDTH = len(_HEADER_LINE)

# Below this magnitude the fractional part IS most of the signal, so a whole
# number would round it away (`0.3→0.8` must not read `0→1`).
_SCALE_DECIMAL_BELOW = 10.0


def _status_code(verdict: Verdict) -> str:
    """The ANSI code for this verdict's glyph, Δ and STATUS — the ONLY three
    cells that are ever painted."""

    if verdict.status == regression.STATUS_REGRESSION:
        return BOLD_RED
    if verdict.status == regression.STATUS_IMPROVEMENT:
        return BOLD_GREEN
    return DIM


def _row_glyph(verdict: Verdict) -> str:
    if verdict.status == regression.STATUS_REGRESSION:
        return GLYPH_OFFENDER
    if verdict.status == regression.STATUS_IMPROVEMENT:
        return GLYPH_OK
    return GLYPH_NEUTRAL


def _status_word(verdict: Verdict) -> str:
    """Uppercase ONLY for a regression — the one status a reader must not
    scan past. Mirrors budget-check, which uppercases exactly its gate
    offenders."""

    if verdict.status == regression.STATUS_REGRESSION:
        return verdict.status.upper()
    return verdict.status.lower()


def _range_decimals(lo: float, hi: float) -> int:
    """How precisely to print one range. Whole numbers keep the column
    scannable, but NEVER at the cost of a lie: a `fps_min` series of
    58.0-58.2 printed `58→58` beside a sparkline that visibly moves, which
    reads as zero variance and contradicts the glyphs right next to it. So
    one decimal is used whenever rounding would collapse two DIFFERENT
    endpoints into the same number, and for sub-10 values where the fraction
    carries the signal. Genuinely equal endpoints stay whole (`100→100`) —
    that collapse is the truth, not a rounding artifact."""

    if max(abs(lo), abs(hi)) < _SCALE_DECIMAL_BELOW:
        return 1
    if lo != hi and f"{lo:.0f}" == f"{hi:.0f}":
        return 1
    return 0


def _min_max(series: Sequence[float]) -> str:
    """The sparkline's scale. `lo→hi` for a real series — INCLUDING a
    zero-variance one, where `100→100` and a flat sparkline say the same true
    thing together. A single point is NOT a range, so it prints as the bare
    value rather than a fabricated `100→100`, and an empty series prints `-`
    like every other missing value in this CLI."""

    if not series:
        return "-"
    lo, hi = min(series), max(series)
    decimals = _range_decimals(lo, hi)
    if len(series) == 1:
        return f"{lo:.{decimals}f}"
    return f"{lo:.{decimals}f}→{hi:.{decimals}f}"


def _metric_row(verdict: Verdict, *, color: bool) -> str:
    code = _status_code(verdict)
    arrow, pct = arrow_and_pct(verdict)
    return "│   " + table_line(
        [
            Cell(_row_glyph(verdict), code),
            verdict.metric_name,
            format_value(verdict.latest_value, verdict.unit),
            format_value(verdict.baseline_value, verdict.unit),
            Cell(f"{arrow} {pct}", code),
            Cell(_status_word(verdict), code),
            sparkline(verdict.series),
            _min_max(verdict.series),
        ],
        _COMPARE_COLUMNS,
        color=color,
    )


def _sanity_label(report: CalibrationReport) -> str:
    if report.status == calibration.STATUS_REASONABLE:
        return f"✓ reasonable — {report.runs_flagged} of {report.runs_total} runs would flag"
    if report.status == calibration.STATUS_TOO_LOOSE:
        return "⚠ too loose — floor suppressed a change your threshold would flag"
    if report.status == calibration.STATUS_TOO_STRICT:
        return "⚠ too strict — normal noise may look like a regression"
    return "· insufficient data to grade config sanity"


# The box rail is "│   " = 4 columns, so 76 + 4 lands on 80 — the narrowest
# terminal this output targets.
_NOTE_TEXT_WIDTH = 76


def _excluded_note(result: CompareResult, *, color: bool) -> list[str]:
    """ONE dim explanatory line (anti-false-positive batch, Task 4) shown
    ONLY when the baseline query silently dropped runs — so a dev iterating
    on a single sha (or on an uncommitted tree) understands WHY history looks
    thin, instead of a bare "insufficient data". Lossy pretty-only; the
    `--json` payload never carries these counts (contract unchanged). Returns
    an EMPTY list when nothing was excluded, so the common case adds no line."""

    same_commit = result.excluded_same_commit
    no_commit = result.excluded_no_commit
    total = same_commit + no_commit
    if total <= 0:
        return []

    clauses: list[str] = []
    if same_commit > 0:
        clauses.append(f"{same_commit} on the current commit")
    if no_commit > 0:
        clauses.append(f"{no_commit} without a git commit")
    detail = ", ".join(clauses)
    text = (
        f"note: {total} run(s) excluded from baseline: {detail} "
        "— commit your changes to grow history"
    )
    # WRAPPED, and styled per line. Unwrapped this reached 128 columns with
    # several excluded runs, which wraps in the terminal itself — and a
    # terminal-wrapped line inside this box loses its `│` rail on every
    # continuation, so the box visibly breaks. Wrapping here keeps the rail on
    # each line. Each line carries its own escape span rather than one span
    # crossing a newline and a rail.
    return [
        style(line, color=color, code=DIM)
        for line in textwrap.wrap(text, _NOTE_TEXT_WIDTH) or [text]
    ]


def _device_label(device_key: str) -> str:
    """The human half of a `model|os|kind` device key. A reader recognizes
    `Pixel 8 Pro`, not the pipe-delimited key, and the OS/kind halves are not
    what distinguishes one compare from another on a dev's machine. Degrades
    the same way `run` does: a key derived with no device attached carries the
    literal `unknown`, which reads as nothing at all in a header, so it
    becomes `unknown device` instead (matching `budget_check_pretty`'s
    `rc.model or "unknown device"`)."""

    model = device_key.split("|")[0].strip()
    if not model or model == "unknown":
        return "unknown device"
    return model


def render_compare(
    result: CompareResult,
    *,
    flow_name: str,
    mode: str,
    device_key: str,
    color: bool = False,
) -> str:
    """The compare table inside an open-right box: a `┌─` header naming WHAT
    was compared (flow, warm/cold mode, device), the labelled column header
    over its rule, one row per metric, then the always-on config-sanity label
    and — only when the baseline dropped runs — the excluded-runs note, both
    inside the box.

    `flow_name`/`mode`/`device_key` are required because a `CompareResult`
    does not carry them and a header cannot be honest without them; the
    caller (`cli/commands/compare.py`) already resolved all three. This
    mirrors `budget_check_pretty.render_summary`, which takes `flow_name` as
    a keyword for the same reason.

    Honors `color=False` (the CLI resolves this from `--no-color`/`NO_COLOR`/
    non-TTY via the shared `OutputContext`) by emitting NO ANSI escapes at
    all."""

    lines: list[str] = [
        f"┌─ perfvibe compare · {flow_name} · {mode} · {_device_label(device_key)}",
        "│",
        f"│   {_HEADER_LINE}",
        f"│   {'─' * _RULE_WIDTH}",
    ]
    lines.extend(_metric_row(verdict, color=color) for verdict in result.verdicts)
    lines.append("│")
    lines.append(f"│   {_sanity_label(result.calibration)}")
    # Every wrapped line gets its own rail, so the box never loses its left
    # edge on a narrow terminal.
    lines.extend(f"│   {note_line}" for note_line in _excluded_note(result, color=color))
    lines.append("└─")
    return "\n".join(lines) + "\n"
