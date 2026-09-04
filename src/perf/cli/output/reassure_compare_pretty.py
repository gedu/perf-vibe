"""Pretty reporter for `perfvibe reassure compare <name>` — human-readable,
LOSSY (SKILL rule 6: `reassure_compare_v1` is the machine contract; this
view MUST NEVER be parsed). Color/TTY-aware via the caller-resolved `color`
flag (golden tests force it off; the CLI resolves it via the shared
`OutputContext`).

LAYOUT: the SAME labelled verdict table `compare_pretty`/`budget_check_
pretty` already draw — a header row over a rule, one row per metric, laid
out by `primitives.table_line` from THIS view's own column spec — inside
the same open-right box (`┌─` top, a `│` left rail, `└─` bottom, never a
right border) every other `reassure` view uses. Unlike those two views,
there is no `min→max` scale column (design "Renderers" row for this
module lists only `table_line`/`header_line`/`Cell`/`arrow_and_pct`/
`format_value`/`sparkline`/`GLYPH_*` — the trailing `TREND` column is the
flexible last one, exactly `budget_check_pretty`'s own shape).

`compare` (PR4b) is the ONE view in the whole `reassure` capability that
renders a verdict at all — every other reassure view only describes
(`reassure_history_pretty.py`'s own module docstring says so explicitly).
**D3 — the load-bearing fact this renderer's caller must honor**: a
`regression` row here is still followed by exit `0` at the CLI layer.
This module only draws the table; it never decides the exit code.

Below the table: ONE D5 sentence line (design "D5 — State Transition in
Both Views", `design.md:376-383`), reusing `cli/output/reassure_show_
pretty.d5_sentence` VERBATIM rather than a second copy of the six-line
wording table — never a table row, never an arrow-and-percentage.

`_row_glyph`/`_status_code`/`_status_word` stay PRIVATE to this module
(design "Renderers": "`compare_pretty.py:94-113` has the `Verdict`-keyed
trio and `budget_check_pretty.py:104` has a `GatedVerdict`-keyed one —
different input types, so reassure is only the SECOND `Verdict`-keyed
caller. Rule of three is not met" — the shared `GLYPH_*` constants already
live in `primitives.py`; a third `Verdict`-keyed caller earns the
promotion of the trio itself, not this one)."""

from __future__ import annotations

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
    sanitize_untrusted_text,
    sparkline,
    table_line,
)
from perf.cli.output.reassure_show_pretty import d5_sentence
from perf.domain import regression
from perf.domain.model import Verdict
from perf.domain.reassure_compare import ReassureComparison

__all__ = ["render_reassure_compare"]

# This view OWNS its spec; only the layout mechanics are shared. Same shape
# as `compare_pretty`/`budget_check_pretty` MINUS the `min→max` scale
# column — design's own "Renderers" row for this module never lists
# `_min_max` among the primitives it reuses.
_COMPARE_COLUMNS: tuple[ColumnSpec, ...] = (
    ("", 1, "<"),  # status glyph — a real column, so the header cannot drift past it
    ("METRIC", 14, "<"),
    ("LATEST", 11, ">"),
    ("BASELINE", 11, ">"),
    ("Δ", 9, ">"),
    ("STATUS", 17, "<"),  # 17 fits `insufficient-data` exactly
    ("TREND", 0, "<"),  # flexible trailing column — the last one, never padded
)

# Derived, never hand-counted: the rule spans exactly the table it underlines.
_HEADER_LINE = header_line(_COMPARE_COLUMNS)
_RULE_WIDTH = len(_HEADER_LINE)


def _status_code(verdict: Verdict) -> str:
    """The ANSI code for this verdict's glyph, Δ and STATUS — the ONLY
    three cells that are ever painted (mirrors `compare_pretty._status_
    code` — this is the SECOND `Verdict`-keyed caller, rule of three not
    met, see module docstring)."""

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
    scan past. Mirrors `compare_pretty`/`budget_check_pretty`, which
    uppercase exactly their own offenders."""

    if verdict.status == regression.STATUS_REGRESSION:
        return verdict.status.upper()
    return verdict.status.lower()


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
        ],
        _COMPARE_COLUMNS,
        color=color,
    )


def render_reassure_compare(comparison: ReassureComparison, *, color: bool = False) -> str:
    """Render `name`'s verdict table inside an open-right box: a `┌─`
    header naming `name` and the baseline import count, the labelled
    column header over its rule, one row per series (`duration_ms` then
    `render_count` — `ReassureComparison.verdicts`'s own fixed order, never
    re-sorted here), then the D5 sentence on its own line below the table
    (design "D5 — State Transition in Both Views") — omitted entirely for
    the one state both D5 views omit (both sides measured `0`).

    Honors `color=False` (the CLI resolves this from `--no-color`/
    `NO_COLOR`/non-TTY via the shared `OutputContext`) by emitting NO ANSI
    escapes at all."""

    lines: list[str] = [
        # W-6: `comparison.name` is attacker-controlled `.perf` content —
        # sanitized before it ever reaches a real terminal.
        f"┌─ perfvibe reassure compare · {sanitize_untrusted_text(comparison.name)} · "
        f"baseline {comparison.baseline_import_n} import(s)",
        "│",
        f"│   {_HEADER_LINE}",
        f"│   {'─' * _RULE_WIDTH}",
    ]
    lines.extend(_metric_row(verdict, color=color) for verdict in comparison.verdicts)
    lines.append("│")

    sentence = d5_sentence(
        comparison.update_count.baseline, comparison.update_count.latest, color=color
    )
    if sentence is not None:
        lines.append(f"│   {sentence}")

    lines.append("└─")
    return "\n".join(lines) + "\n"
