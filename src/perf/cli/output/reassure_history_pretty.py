"""Pretty reporter for `perfvibe reassure history <name>` — human-readable,
LOSSY (SKILL rule 6: `reassure_history_v1` is the machine contract; this
view MUST NEVER be parsed). Color/TTY-aware via the caller-resolved `color`
flag (golden tests force it off; the CLI resolves it via the shared
`OutputContext`).

LAYOUT: the same open-right box every other reassure view draws (`┌─` top,
a `│` left rail, `└─` bottom, never a right border) around TWO independent
sections, one per series — design "Renderers" `reassure_history_pretty.py`
row: "two per-series sections... invariant I1 surfacing visually". Each
section is a copy, section-for-section, of `history_pretty._metric_section`
(`cli/output/history_pretty.py:233-254`): a header line carrying the whole
window as a `sparkline`, then a `chart_lines` chart and a table over the
most recent `_MAX_TABLE_ROWS` points.

WHY TWO SECTIONS AND NEVER ONE SHARED CHART. `duration` (reassure's
outlier-filtered set) and `count` (its unfiltered post-warmup set) are
different lengths, measured over different runs — I1 again, in chart form.
Zipping them into one chart with two lines, sharing a y-axis, or
interleaving their table rows would all imply the points correspond, and
they do not. So this view draws two separate, self-contained sections —
never a combined one.

WHY THE DELTA COLUMN FROM `history_pretty` IS NOT HERE. `history` grades
runs against `default_higher_is_better`; that lookup is keyed by FLOW
metric names and reassure's series (`duration_ms`/`render_count`) are not
in it. Rather than guess a direction, this view — like every other
`reassure` view (D3: no gate in v1) — only describes, never judges: no
arrow, no color-coded direction, no `Δ` column. `compare` (PR4b) is the
one view in this capability that renders a verdict at all.

X-AXIS LABELS: short `commit_hash` when present, else the date part of
`ordered_at`, else `#<import_id>` — never the bare import id alone when a
label exists, and never nothing. `commit_hash`/`created_date` are both
nullable columns (`db/migrations/0005_add_reassure_tables.sql`), so a real
window can land on either of the first two levels; the third
(`#<import_id>`) exists for completeness (an `ordered_at` this view is
handed empty) even though `imported_at`'s own insert path
(`store_sqlite.py`'s `self._clock.now_utc_iso()`) never leaves it empty on
a real row — see `tests/golden/test_reassure_history_pretty_golden.py`'s
module docstring for the full reachability note."""

from __future__ import annotations

from collections.abc import Sequence

from perf.cli.output.primitives import (
    BOLD,
    CHART_COL_W,
    DIM,
    ColumnSpec,
    chart_lines,
    format_value,
    header_line,
    sparkline,
    style,
    table_line,
)
from perf.domain.model import HistoryMetric, ReassureSeriesPoint

__all__ = ["render_reassure_history"]

# The most recent points shown per series — in BOTH the chart and the
# table, mirroring `history_pretty._MAX_TABLE_ROWS` exactly (same value,
# same reasoning: a bar and the row under it must always be the same
# point). The header sparkline still spans the whole queried window.
_MAX_TABLE_ROWS = 8

# This view OWNS its spec; only the layout mechanics are shared
# (`primitives.table_line`). No `Δ` column — see the module docstring.
_HISTORY_COLUMNS: tuple[ColumnSpec, ...] = (
    ("IMPORT", 8, "<"),
    ("DATE", 10, "<"),
    ("COMMIT", 7, "<"),
    ("P50", 10, ">"),
    ("P90", 10, ">"),
    ("N", 6, ">"),
)

_HEADER_LINE = header_line(_HISTORY_COLUMNS)
_RULE_WIDTH = len(_HEADER_LINE)

_SERIES_LABELS: tuple[str, str] = ("duration", "count")


def _short_commit(commit: str | None) -> str:
    """Stays local rather than joining `primitives.py`, matching
    `history_pretty._short_commit`'s own reasoning: the MISSING-value
    fallback (`-`) is this table's own rendering choice, not a shared
    primitive."""

    return "-" if not commit else commit[:7]


def _date_part(ordered_at: str) -> str:
    """The date portion of an ISO-8601 timestamp (everything before the
    'T'), falling back to the whole string if it has no time component —
    identical to `history_pretty._date_part`."""

    return ordered_at.split("T", 1)[0]


def _chart_label(point: ReassureSeriesPoint) -> str:
    """The x-axis label for one point: short `commit_hash` when present,
    else the date part of `ordered_at`, else `#<import_id>` — see the
    module docstring for exactly which levels real store data can reach."""

    if point.commit_hash:
        return point.commit_hash[:7]
    if point.ordered_at:
        return _date_part(point.ordered_at)
    return f"#{point.import_id}"


def _metric_for(point: ReassureSeriesPoint, series: str) -> HistoryMetric | None:
    return point.entry.duration if series == "duration" else point.entry.count


def _p90_series(points: Sequence[ReassureSeriesPoint], series: str) -> list[float]:
    """Every recorded p90 for `series`, gaps DROPPED — a point that never
    measured this series is not a zero, and charting it as one would
    invent a cliff that never happened."""

    return [
        metric.p90
        for point in points
        if (metric := _metric_for(point, series)) is not None and metric.p90 is not None
    ]


def _unit_for(points: Sequence[ReassureSeriesPoint], series: str) -> str:
    for point in points:
        metric = _metric_for(point, series)
        if metric is not None:
            return metric.unit
    return ""


def _chart_section(points: Sequence[ReassureSeriesPoint], series: str) -> list[str]:
    """The y-axis chart of p90 over `points` for ONE series, behind this
    view's rail. Points without a p90 for this series are absent from BOTH
    the bars and the labels, so every label still sits under its own bar —
    and this section never reaches into the OTHER series' data.

    `col_w` is widened past `primitives.CHART_COL_W`'s default (8) when a
    label needs it: `history_pretty`'s own labels (a 7-char short sha, or
    `r<run_id>`) always fit inside 8, but this view's date fallback is a
    full `YYYY-MM-DD` (10 chars) — at the default width that 10-char label
    would overrun into the next column with no separator at all, which is
    exactly the "off-by-one shows up as a mislabelled axis" trap the
    fallback chain's own tests guard against. Computed from the ACTUAL
    labels charted, so a window that never reaches the date fallback still
    renders at the tighter default width."""

    charted: list[tuple[str, float]] = []
    for point in points:
        metric = _metric_for(point, series)
        if metric is None or metric.p90 is None:
            continue
        charted.append((_chart_label(point), metric.p90))

    if not charted:
        return [f"│   (no chart — no p90 recorded for {series} in this window)"]

    labels = [label for label, _ in charted]
    col_w = max(CHART_COL_W, max(len(label) for label in labels) + 1)

    lines = chart_lines(
        [value for _, value in charted],
        labels,
        col_w=col_w,
    )
    return [f"│   {line}" for line in lines]


def _table_section(points: Sequence[ReassureSeriesPoint], series: str, *, color: bool) -> list[str]:
    lines = [f"│   {_HEADER_LINE}", f"│   {'─' * _RULE_WIDTH}"]

    for point in points:
        metric = _metric_for(point, series)
        lines.append(
            "│   "
            + table_line(
                [
                    str(point.import_id),
                    _date_part(point.ordered_at) if point.ordered_at else "-",
                    _short_commit(point.commit_hash),
                    format_value(metric.p50 if metric is not None else None),
                    format_value(metric.p90 if metric is not None else None),
                    str(metric.n) if metric is not None else "-",
                ],
                _HISTORY_COLUMNS,
                color=color,
            )
        )
    return lines


def _section(
    points: Sequence[ReassureSeriesPoint],
    series: str,
    *,
    total_points: int,
    color: bool,
) -> list[str]:
    """One series: a header line carrying the WHOLE window as a sparkline,
    then the chart and the table over the most recent `_MAX_TABLE_ROWS`
    points — copied section-for-section from
    `history_pretty._metric_section`."""

    unit = _unit_for(points, series)
    window = _p90_series(points, series)
    recent = points[-_MAX_TABLE_ROWS:]

    label = f"{series} ({unit})" if unit else series
    header = style(label, color=color, code=BOLD)
    window_bits = [f"window {sparkline(window)}"] if window else []
    if len(recent) < total_points:
        window_bits.append(f"charting the last {len(recent)} of {total_points} imports")
    trailer = style("  ·  ".join(window_bits), color=color, code=DIM) if window_bits else ""

    lines = [f"│   {header}   {trailer}".rstrip()]
    lines.append("│")
    lines.extend(_chart_section(recent, series))
    lines.append("│")
    lines.extend(_table_section(recent, series, color=color))
    return lines


def render_reassure_history(
    name: str, points: Sequence[ReassureSeriesPoint], *, color: bool = False
) -> str:
    """Render `name`'s full series (already OLDEST→NEWEST, one point per
    import containing `name` — the store's job, D2) inside an open-right
    box: a `┌─` header naming `name` and the point count, then TWO
    independent sections, one for `duration`, one for `count` — NEVER a
    single combined chart or table (invariant I1, surfaced here).

    Honors `color=False` (the CLI resolves this from `--no-color`/
    `NO_COLOR`/non-TTY via the shared `OutputContext`) by emitting NO ANSI
    escapes at all."""

    lines: list[str] = [
        f"┌─ perfvibe reassure history · {name} · {len(points)} import(s)",
        "│",
    ]
    for series in _SERIES_LABELS:
        lines.extend(_section(points, series, total_points=len(points), color=color))
        lines.append("│")
    lines.append("└─")
    return "\n".join(lines) + "\n"
