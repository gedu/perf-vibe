"""Pretty reporter for `perfvibe reassure entries <import-id>` —
human-readable, LOSSY (SKILL rule 6: the `--json` `reassure_entries_v1`
payload is the machine contract; this view must NEVER be parsed).
Color/TTY-aware via the caller-resolved `color` flag (golden tests force
it off; the CLI resolves it via the shared `OutputContext`).

LAYOUT: the same open-right box `list`/`compare`/`history`/`budget-check`
draw — `┌─` top, a `│` left rail, `└─` bottom, and NEVER a right border
(design "Renderers").

INVARIANT I1, SURFACED IN THE UI: duration and count are two
INDEPENDENTLY-indexed series (`durations` is outlier-FILTERED, `counts` is
the UNFILTERED post-warmup set — `adapters/reassure_jsonl.py`'s module
docstring), so their p50/p90/n are drawn as two SEPARATE column groups
(`DUR P50`/`DUR P90`/`DUR N` and `CNT P50`/`CNT P90`/`CNT N`) rather than
one shared pair of columns — a single "P50"/"P90" pair would visually
imply the two series are index-aligned, which they are not. A series with
zero stored samples for an entry renders as `-` in every one of its three
columns (`ReassureEntryRow.duration`/`.count` is `None`, never a
zero-valued `HistoryMetric` standing in for "no series") — never a `0.0`,
which would read as a real, measured zero.

Takes the RAW `Sequence[ReassureEntryRow]` (never the `--json` payload),
mirroring `reassure_list_pretty.render_reassure_list`'s shape — the same
`history_pretty`-style "raw row in, rendered string out" contract, not
`reassure_import_pretty`'s "build-payload-then-render" shape. `import_id`
is passed explicitly (`ReassureEntryRow` has no `import_id` field — it
describes one entry within an import already identified by the caller)."""

from __future__ import annotations

from collections.abc import Sequence

from perf.cli.output.primitives import (
    Cell,
    ColumnSpec,
    header_line,
    table_line,
)
from perf.domain.model import HistoryMetric, ReassureEntryRow

__all__ = ["render_reassure_entries"]

# This view OWNS its spec; only the layout mechanics are shared
# (`primitives.table_line`/`header_line`) — mirrors `reassure_list_pretty`'s
# `_LIST_COLUMNS`. `DUR *` and `CNT *` are two separate three-column groups
# (I1, surfaced here) — never one shared P50/P90/N pair.
_ENTRIES_COLUMNS: tuple[ColumnSpec, ...] = (
    ("NAME", 40, "<"),
    ("TYPE", 8, "<"),
    ("RUNS", 4, ">"),
    ("DUR P50", 8, ">"),
    ("DUR P90", 8, ">"),
    ("DUR N", 6, ">"),
    ("CNT P50", 8, ">"),
    ("CNT P90", 8, ">"),
    ("CNT N", 6, ">"),
)

# Derived, never hand-counted: the rule spans exactly the table it underlines.
_HEADER_LINE = header_line(_ENTRIES_COLUMNS)
_RULE_WIDTH = len(_HEADER_LINE)


def _value_cell(value: float | None) -> str:
    """One decimal place, or `-` for a missing value — stays LOCAL rather
    than joining `primitives.format_value` because this view never passes
    a `unit` (the column header already names the series; a per-cell unit
    would repeat `ms`/`count` on every row of an already-labelled
    column)."""

    return "-" if value is None else f"{value:.1f}"


def _n_cell(n: int | None) -> str:
    return "-" if n is None else str(n)


def _metric_cells(metric: HistoryMetric | None) -> tuple[str, str, str]:
    """The (p50, p90, n) triple for one series — ALL THREE render `-`
    together when the series has zero stored samples (`metric is None`),
    never a zero-valued `HistoryMetric` standing in for "no series"."""

    if metric is None:
        return "-", "-", "-"
    return _value_cell(metric.p50), _value_cell(metric.p90), _n_cell(metric.n)


def _row_line(row: ReassureEntryRow, *, color: bool) -> str:
    duration_p50, duration_p90, duration_n = _metric_cells(row.duration)
    count_p50, count_p90, count_n = _metric_cells(row.count)
    return "│   " + table_line(
        [
            Cell(row.name),
            row.entry_type,
            str(row.runs),
            duration_p50,
            duration_p90,
            duration_n,
            count_p50,
            count_p90,
            count_n,
        ],
        _ENTRIES_COLUMNS,
        color=color,
    )


def render_reassure_entries(
    import_id: int, entries: Sequence[ReassureEntryRow], *, color: bool = False
) -> str:
    """Render one import's entries (already fetched by the caller, in
    store order) inside an open-right box: a `┌─` header naming the
    import and the count, the labelled column header over its rule, one
    row per entry — or a single explanatory line when the import has zero
    entries (a real, valid state — never confused with an unknown import
    id, which the CLI rejects before this renderer is ever called).

    Honors `color=False` (the CLI resolves this from `--no-color`/
    `NO_COLOR`/non-TTY via the shared `OutputContext`) by emitting NO ANSI
    escapes at all — every cell in this view is plain text today, but the
    `color` parameter stays for parity with every other renderer in this
    package."""

    lines: list[str] = [
        f"┌─ perfvibe reassure entries · import {import_id} · {len(entries)} entrie(s)",
        "│",
    ]
    if not entries:
        lines.append("│   (this import has zero entries)")
    else:
        lines.append(f"│   {_HEADER_LINE}")
        lines.append(f"│   {'─' * _RULE_WIDTH}")
        lines.extend(_row_line(row, color=color) for row in entries)
    lines.append("│")
    lines.append("└─")
    return "\n".join(lines) + "\n"
