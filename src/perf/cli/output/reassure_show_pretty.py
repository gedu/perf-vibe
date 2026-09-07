"""Pretty reporter for `perfvibe reassure show <name>` — human-readable,
LOSSY (SKILL rule 6: `reassure_show_v1` is the machine contract; this view
MUST NEVER be parsed). Color/TTY-aware via the caller-resolved `color`
flag (golden tests force it off; the CLI resolves it via the shared
`OutputContext`).

LAYOUT: the same open-right box every other reassure view draws (`┌─` top,
a `│` left rail, `└─` bottom, never a right border) plus a labelled
key/value block — `render_reassure_import`'s confirmation shape (design
"Renderers" table, `reassure_show_pretty.py` row), NOT a table.

D5, THE ONE SENTENCE LINE (design "D5 — State Transition in Both Views",
`design.md:376-383` — the exact wording table, used VERBATIM, glyphs and
dimming included, never rephrased): never a table row, never an
arrow-and-percentage.
  - `introduced` -> `✗ extra mount render introduced (B -> L)`
  - `resolved`   -> `✓ extra mount render resolved (B -> L)`
  - `changed`    -> `✗ mount render count changed (B -> L)`
  - `unchanged`, BOTH ZERO -> omitted entirely (a clean run reporting a
    clean run is not news)
  - `unchanged`, both non-zero -> `· mount render count unchanged (N)`
    (dim)
  - `unknown` -> `· mount render diagnostics unavailable (not measured in
    one of the two imports)` (dim)

This split — `unchanged` has TWO different renderings depending on
whether both sides are zero, and `unknown` is a SIXTH distinct rendering
for the same five states — is deliberately reproduced from the design
table verbatim rather than re-derived, since it is the row the design
itself calls out as "most easily missed".

Also prints a `declared runs N != stored n M` line per series, but ONLY
on disagreement — surfacing, never repairing, the ingest mismatch
`ReassureEntryRow`'s own docstring describes (I2 corollary; `runs` stays
declared-only, forever)."""

from __future__ import annotations

from perf.cli.output.primitives import (
    DIM,
    Cell,
    ColumnSpec,
    format_value,
    style,
    table_line,
)
from perf.domain.model import HistoryMetric, ReassureEntryRow
from perf.domain.reassure_compare import derive_update_count_change

__all__ = ["render_reassure_show"]

# Label/value pairs, not a real table — mirrors `reassure_import_pretty`'s
# `_COUNTER_COLUMNS` shape; this view owns its own widths.
_KV_COLUMNS: tuple[ColumnSpec, ...] = (
    ("", 20, "<"),
    ("", 16, ">"),
)


def _metric_rows(label: str, metric: HistoryMetric | None) -> list[tuple[str, str]]:
    """ALL THREE rows render `-` together when the series has zero stored
    samples (`metric is None`), never a zero-valued `HistoryMetric`
    standing in for "no series" — mirrors
    `reassure_entries_pretty._metric_cells`."""

    if metric is None:
        return [(f"{label} p50", "-"), (f"{label} p90", "-"), (f"{label} n", "-")]
    return [
        (f"{label} p50", format_value(metric.p50, metric.unit)),
        (f"{label} p90", format_value(metric.p90, metric.unit)),
        (f"{label} n", str(metric.n)),
    ]


def _d5_sentence(baseline: int | None, latest: int | None, *, color: bool) -> str | None:
    """The exact-wording table from `design.md:376-383`. Returns `None`
    for the one state this view omits entirely: both sides measured at
    `0` (`'unchanged'` with `latest == 0`) — nothing ever went wrong and
    nothing changed, so there is no line to print."""

    change = derive_update_count_change(baseline, latest)
    if change.state == "introduced":
        return f"✗ extra mount render introduced ({change.baseline} -> {change.latest})"
    if change.state == "resolved":
        return f"✓ extra mount render resolved ({change.baseline} -> {change.latest})"
    if change.state == "changed":
        return f"✗ mount render count changed ({change.baseline} -> {change.latest})"
    if change.state == "unknown":
        return style(
            "· mount render diagnostics unavailable (not measured in one of the two imports)",
            color=color,
            code=DIM,
        )
    # state == "unchanged"
    if change.latest == 0:
        return None
    return style(f"· mount render count unchanged ({change.latest})", color=color, code=DIM)


def _runs_mismatch_lines(entry: ReassureEntryRow) -> list[str]:
    """A `declared runs N != stored n M` line per series, ONLY on
    disagreement — `runs` (declared) is NEVER reconciled/repaired, only
    surfaced (I2 corollary, `ReassureEntryRow`'s own docstring)."""

    lines: list[str] = []
    if entry.duration is not None and entry.duration.n != entry.runs:
        lines.append(f"declared runs {entry.runs} != stored n {entry.duration.n} (duration)")
    if entry.count is not None and entry.count.n != entry.runs:
        lines.append(f"declared runs {entry.runs} != stored n {entry.count.n} (count)")
    return lines


def render_reassure_show(
    import_id: int,
    entry: ReassureEntryRow,
    baseline_initial_update_count: int | None,
    *,
    color: bool = False,
) -> str:
    """Render `name`'s detail for one import (D8 default or `--import
    <id>` override — already resolved by the caller): a labelled
    key/value block for entry type, declared `runs`, and the two
    independent series summaries, followed by the D5 sentence (design
    "D5 — State Transition in Both Views") and — only on disagreement —
    the `declared runs N != stored n M` line(s).

    Honors `color=False` (the CLI resolves this from `--no-color`/
    `NO_COLOR`/non-TTY via the shared `OutputContext`) by emitting NO ANSI
    escapes at all."""

    kv_rows: list[tuple[str, str]] = [
        ("entry type", entry.entry_type),
        ("runs (declared)", str(entry.runs)),
        *_metric_rows("duration", entry.duration),
        *_metric_rows("count", entry.count),
    ]

    blocks: list[list[str]] = [
        [
            "│   " + table_line([label, Cell(value)], _KV_COLUMNS, color=color)
            for label, value in kv_rows
        ]
    ]

    sentence = _d5_sentence(baseline_initial_update_count, entry.initial_update_count, color=color)
    if sentence is not None:
        blocks.append([f"│   {sentence}"])

    mismatch_lines = _runs_mismatch_lines(entry)
    if mismatch_lines:
        blocks.append([f"│   {line}" for line in mismatch_lines])

    lines: list[str] = [
        f"┌─ perfvibe reassure show · {entry.name} · import {import_id}",
        "│",
    ]
    for block in blocks:
        lines.extend(block)
        lines.append("│")
    lines.append("└─")
    return "\n".join(lines) + "\n"
