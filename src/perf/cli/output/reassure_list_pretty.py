"""Pretty reporter for `perfvibe reassure list` — human-readable, LOSSY
(SKILL rule 6: the `--json` `reassure_list_v1` payload is the machine
contract; this view must NEVER be parsed). Color/TTY-aware via the
caller-resolved `color` flag (golden tests force it off; the CLI resolves
it via the shared `OutputContext`).

LAYOUT: the same open-right box `compare`/`history`/`budget-check` draw —
`┌─` top, a `│` left rail, `└─` bottom, and NEVER a right border
(design "Renderers").

Takes the RAW `Sequence[ReassureImportRow]`, never the `--json` payload —
mirrors `history_pretty.render_history(..., runs, ...)`, not
`reassure_import_pretty`'s "build the payload, then render the payload"
shape. `ReassureImportRow.ordering_key` names WHICH D2 key ordered a row
(`'created_date'` | `'imported_at'`) so this view can dim a row whose date
column is showing the `imported_at` FALLBACK rather than a real
`created_date` — that field is deliberately absent from the `--json`
contract (mechanically derivable from `created_date`/`imported_at`, the
same no-second-source-of-truth reasoning that refused `reassure_import_v1`'s
`zero_entries`), so the pretty view reads it straight off the row instead.
"""

from __future__ import annotations

from collections.abc import Sequence

from perf.cli.output.primitives import (
    DIM,
    Cell,
    ColumnSpec,
    header_line,
    sanitize_untrusted_text,
    style,
    table_line,
)
from perf.domain.model import ReassureImportRow

__all__ = ["render_reassure_list"]

# This view OWNS its spec; only the layout mechanics are shared
# (`primitives.table_line`/`header_line`) — mirrors `history_pretty`'s
# `_HISTORY_COLUMNS`.
_LIST_COLUMNS: tuple[ColumnSpec, ...] = (
    ("IMPORT", 6, "<"),
    ("DATE", 10, "<"),  # created_date (or imported_at, dimmed, on fallback)
    ("BRANCH", 12, "<"),  # LABEL only — never a key/filter/join
    ("COMMIT", 7, "<"),  # short sha, LABEL only
    ("ENTRIES", 7, ">"),
)

# Derived, never hand-counted: the rule spans exactly the table it underlines.
_HEADER_LINE = header_line(_LIST_COLUMNS)
_RULE_WIDTH = len(_HEADER_LINE)


def _short_commit(commit: str | None) -> str:
    """Stays LOCAL rather than joining `output/primitives.py` — mirrors
    `history_pretty._short_commit`'s own reasoning for staying local (its
    module docstring: different views may want different missing-value
    fallbacks even though the `[:7]` truncation is shared, so this is not
    yet a third caller of one shared primitive). W-6: `commit_hash` is
    attacker-controlled `.perf` header content — sanitized before
    truncation."""

    return "-" if not commit else sanitize_untrusted_text(commit)[:7]


def _date_part(value: str) -> str:
    """The date portion of an ISO-8601 timestamp (everything before the
    'T'), falling back to the whole string if it has no time component —
    mirrors `history_pretty._date_part`."""

    return value.split("T", 1)[0]


def _branch_cell(branch: str | None) -> str:
    # W-6: `branch` is attacker-controlled `.perf` header content — LABEL
    # ONLY (nothing keys/filters/joins on it), sanitized before rendering.
    return sanitize_untrusted_text(branch) if branch else "-"


def _date_cell(row: ReassureImportRow) -> Cell:
    """The effective ordering date, dimmed when it fell back to
    `imported_at` (design: "`ordering_key` shown dim when it fell back to
    `imported_at`") — never dimmed when it IS a real `created_date`."""

    text = _date_part(row.ordered_at)
    return Cell(text, DIM if row.ordering_key == "imported_at" else "")


def _row_line(row: ReassureImportRow, *, color: bool) -> str:
    return "│   " + table_line(
        [
            str(row.import_id),
            _date_cell(row),
            _branch_cell(row.branch),
            _short_commit(row.commit_hash),
            str(row.entry_count),
        ],
        _LIST_COLUMNS,
        color=color,
    )


def render_reassure_list(imports: Sequence[ReassureImportRow], *, color: bool = False) -> str:
    """Render the import roster (already ordered per D2, most recent
    first, and already limited by the caller) inside an open-right box: a
    `┌─` header naming the count, the labelled column header over its
    rule, one row per import — or a single explanatory line when the
    roster is empty.

    Honors `color=False` (the CLI resolves this from `--no-color`/
    `NO_COLOR`/non-TTY via the shared `OutputContext`) by emitting NO ANSI
    escapes at all."""

    lines: list[str] = [
        f"┌─ perfvibe reassure list · {len(imports)} import(s)",
        "│",
    ]
    if not imports:
        lines.append(
            style(
                "│   (no imports recorded yet — run `perfvibe reassure import` first)",
                color=color,
                code=DIM,
            )
        )
    else:
        lines.append(f"│   {_HEADER_LINE}")
        lines.append(f"│   {'─' * _RULE_WIDTH}")
        lines.extend(_row_line(row, color=color) for row in imports)
    lines.append("│")
    lines.append("└─")
    return "\n".join(lines) + "\n"
