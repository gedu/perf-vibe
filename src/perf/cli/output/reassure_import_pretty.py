"""Pretty confirmation for `perfvibe reassure-import` — human-readable, LOSSY
(it summarizes and truncates the content hash; it must NEVER be parsed — SKILL
rule 6; the flat `reassure_import_v1` payload is the machine contract).

WHY THIS MODULE EXISTS. The confirmation used to be a private
`_render_import_pretty` inside `cli/commands/reassure_import.py` that printed
the `--json` payload back as nine `key: value` lines — including
`already_imported: False` (a Python bool, in a sentence aimed at a person) and
a 64-character hex hash on a line of its own. That is the machine contract
leaking into the human view: a reader had to know the schema to read it, and an
integration test was asserting on the literal string `entries_imported`, which
pinned the leak in place. Every other pretty renderer in this CLI lives in
`cli/output/`, so this one now does too.

LAYOUT: the same OPEN-RIGHT box `compare`, `history` and `budget-check` draw
(`┌─` top, a `│` left rail, `└─` bottom, and NEVER a right border), with the
path and kind in the header where the "what did I just do" context belongs.

TWO OUTCOMES, TOLD APART. A byte-identical re-import returns
`already_imported: true` with every `*_imported` counter `0` by construction,
so the old view rendered a wall of zeros that read like a failed import. It now
says so in one line and skips the counters entirely — there is nothing to count.
"""

from __future__ import annotations

from typing import Any

from perf.cli.output.primitives import (
    DIM,
    GLYPH_NEUTRAL,
    GLYPH_OK,
    YELLOW,
    Cell,
    ColumnSpec,
    style,
    table_line,
)

__all__ = ["render_reassure_import"]

# A label/value pair list, not a real table — so no header row and no rule. It
# still goes through `table_line` rather than a hand-spaced f-string so the
# widths are declared in one place a reader can find.
_COUNTER_COLUMNS: tuple[ColumnSpec, ...] = (
    ("", 26, "<"),
    ("", 7, ">"),
)

# Enough hex to recognize a hash across two runs, nowhere near enough to invite
# anyone to parse it out of the pretty view.
_HASH_CHARS = 12


def _counter_rows(payload: dict[str, Any], *, color: bool) -> list[str]:
    """The four `*_imported` counters. `entries_with_render_issues` is painted
    when it is NON-ZERO: it is the one counter that is a FINDING rather than a
    volume — reassure spotted an extra render on mount — and a reader scanning
    four numbers has no other reason to stop on it."""

    issues = payload["entries_with_render_issues"]
    rows: list[tuple[str, str, str]] = [
        ("entries", str(payload["entries_imported"]), ""),
        ("duration samples", str(payload["duration_samples_imported"]), ""),
        ("count samples", str(payload["count_samples_imported"]), ""),
        ("entries with render issues", str(issues), YELLOW if issues else ""),
    ]
    return [
        "│   " + table_line([label, Cell(value, code)], _COUNTER_COLUMNS, color=color)
        for label, value, code in rows
    ]


def render_reassure_import(payload: dict[str, Any], *, color: bool = False) -> str:
    """Render the `reassure-import` confirmation for `payload` (the same dict
    `build_reassure_import_payload` returns).

    Honors `color=False` (the CLI resolves this from `--no-color`/`NO_COLOR`/
    non-TTY via the shared `OutputContext`) by emitting NO ANSI escapes at all.
    Reads only keys the contract guarantees, so a `schema_version` bump that
    ADDS a key cannot break this view."""

    lines: list[str] = [
        f"┌─ perfvibe reassure-import · {payload['kind']} · {payload['path']}",
        "│",
    ]

    if payload["already_imported"]:
        # Every counter is 0 by construction here, so printing them would be
        # four zeros pretending to be a result.
        lines.append(
            "│   "
            + style(
                f"{GLYPH_NEUTRAL}  already imported — nothing re-persisted",
                color=color,
                code=DIM,
            )
        )
    else:
        imported = payload["entries_imported"]
        # A readable file that recovered NOTHING exits 0 and warns on stderr,
        # so it is not a failure — but it is not a `✓` either, and the old view
        # had no glyph at all to get wrong. Neutral glyph, dimmed.
        glyph = GLYPH_OK if imported else GLYPH_NEUTRAL
        lines.append(
            "│   "
            + style(
                f"{glyph}  {imported} entr{'y' if imported == 1 else 'ies'} imported",
                color=color,
                code="" if imported else DIM,
            )
        )
        lines.append("│")
        lines.extend(_counter_rows(payload, color=color))

    lines.append("│")
    skipped = payload["entries_skipped"]
    if skipped:
        # The per-line reasons are stderr-only by contract, so the count here
        # would be a dead end without saying where the detail went.
        lines.append(
            "│   "
            + style(
                f"{GLYPH_NEUTRAL} {skipped} line(s) skipped — reasons on stderr",
                color=color,
                code=DIM,
            )
        )
    lines.append(
        "│   "
        + style(
            f"{GLYPH_NEUTRAL} content {payload['content_hash'][:_HASH_CHARS]}",
            color=color,
            code=DIM,
        )
    )
    lines.append("└─")
    return "\n".join(lines) + "\n"
