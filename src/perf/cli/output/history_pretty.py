"""Pretty reporter for `perf history <flow>` — human-readable, LOSSY (it
summarizes and truncates the run table; it must NEVER be parsed — SKILL
rule 6; the `--json` `history_v1` payload is the machine contract).
Color/TTY-aware via the caller-resolved `color` flag (golden tests force it
off; the CLI resolves it via the shared `OutputContext`).

LAYOUT: the same OPEN-RIGHT box `compare` and `budget-check` draw — `┌─` top,
a `│` left rail, `└─` bottom, and NEVER a right border. One section per
metric, each with three parts.

WHY A CHART AND NOT JUST A SPARKLINE. The previous shape had ONE sparkline per
metric and nothing else, so `▁▂▁█` said "the last run was the worst" and
nothing about how far — 100→110 and 812→1310 draw identically, and there was
no min, no max, and no axis anywhere on the page. `budget-check`'s detail view
already solved this with a labelled y-axis chart, so this view now draws the
SAME chart from `primitives.chart_lines` rather than a second implementation
of tick arithmetic. The full-window sparkline stays on the metric's header
line, because the chart is capped to the most recent runs and the sparkline is
the only thing that still shows the whole window at a glance.

WHY THE CHART AND THE TABLE SHOW THE SAME RUNS. They used to disagree in
silence: the sparkline spanned the whole queried window while the table showed
only the last 8 rows, so a reader lining up a bar with a row was lining up two
different series. Both now render `_MAX_TABLE_ROWS` runs, and when that
truncates the window the box says so out loud.

WHY THE Δ COLUMN IS A HINT AND THE ARROW IS THE SIGNAL. `history` describes;
it does not judge — so unlike `compare` there is no status word, no `✗`/`✓`,
and no verdict of any kind here. The arrow carries DIRECTION, which is a plain
fact about two numbers. The color carries whether that direction is good for
this metric (`default_higher_is_better`, so a falling `fps_avg` is red while a
falling `checkout` is green), which is an interpretation. Losing the color to
`--no-color` therefore loses no fact — the arrow, the percentage and the
metric name are all still right there.
"""

from __future__ import annotations

from collections.abc import Sequence

from perf.cli.output.primitives import (
    ARROW_DOWN,
    ARROW_FLAT,
    ARROW_NONE,
    ARROW_UP,
    BOLD,
    BOLD_GREEN,
    BOLD_RED,
    DIM,
    Cell,
    ColumnSpec,
    chart_lines,
    device_label,
    format_value,
    header_line,
    sparkline,
    style,
    table_line,
)
from perf.domain.model import HistoryMetric, HistoryRun, default_higher_is_better

__all__ = ["render_history"]

# The most recent runs shown per metric — in BOTH the chart and the table, so a
# bar and the row under it are always the same run. The header sparkline still
# spans the whole queried window.
_MAX_TABLE_ROWS = 8

# This view OWNS its spec; only the layout mechanics are shared
# (`primitives.table_line`). Every column is fixed-width so the header and the
# rows are laid out from ONE source and cannot drift apart.
_HISTORY_COLUMNS: tuple[ColumnSpec, ...] = (
    ("RUN", 6, "<"),
    ("DATE", 10, "<"),  # exactly an ISO date
    ("COMMIT", 7, "<"),  # exactly a short sha
    ("P50", 10, ">"),
    ("P90", 10, ">"),
    ("Δ P90", 10, ">"),
)

# Derived, never hand-counted: the rule spans exactly the table it underlines.
_HEADER_LINE = header_line(_HISTORY_COLUMNS)
_RULE_WIDTH = len(_HEADER_LINE)


def _short_commit(commit: str | None) -> str:
    """Stays local rather than joining `output/primitives.py`: it looks like
    `budget_check_pretty._short_sha` but its MISSING-value fallback differs
    (`-` in this table, `unknown` in budget-check's header). The fallback is
    the user-visible half, so these are two different renderings that happen
    to share a `[:7]`, not one shared primitive."""

    return "-" if not commit else commit[:7]


def _chart_label(run: HistoryRun) -> str:
    """The x-axis label for one bar. A short sha when there is one, and the run
    id otherwise — NOT the table's `-`, because a window of uncommitted runs
    would then label every bar identically and the chart would say nothing
    about which run is which."""

    return run.git_commit[:7] if run.git_commit else f"r{run.run_id}"


def _date_part(started_at: str) -> str:
    """The date portion of an ISO-8601 timestamp (everything before the
    'T'), falling back to the whole string if it has no time component."""

    return started_at.split("T", 1)[0]


def _metric_names(runs: Sequence[HistoryRun]) -> list[str]:
    """Every metric name observed anywhere in the window, sorted — the set
    of per-metric sections to render."""

    names: set[str] = set()
    for run in runs:
        for metric in run.metrics:
            names.add(metric.metric_name)
    return sorted(names)


def _metric_in_run(run: HistoryRun, metric_name: str) -> HistoryMetric | None:
    for metric in run.metrics:
        if metric.metric_name == metric_name:
            return metric
    return None


def _unit_for(runs: Sequence[HistoryRun], metric_name: str) -> str:
    for run in runs:
        metric = _metric_in_run(run, metric_name)
        if metric is not None:
            return metric.unit
    return ""


def _p90_series(runs: Sequence[HistoryRun], metric_name: str) -> list[float]:
    """Every recorded p90 for `metric_name`, gaps DROPPED — a run that never
    measured this metric is not a zero, and charting it as one would invent a
    cliff that never happened."""

    return [
        metric.p90
        for run in runs
        if (metric := _metric_in_run(run, metric_name)) is not None and metric.p90 is not None
    ]


def _delta_cell(metric_name: str, current: float | None, previous: float | None) -> Cell:
    """The per-run Δ: `current` against the last run that HAD a value, as an
    arrow plus a signed percentage.

    `-` whenever there is nothing honest to divide: no current value, no
    previous value, or a previous value of exactly zero (a percentage change
    from zero is undefined, and printing `+inf%` or a bare `+100%` would be a
    made-up number).

    The color is direction-aware via `default_higher_is_better`, so a rising
    `checkout` is red while a rising `fps_avg` is green. See the module
    docstring: the arrow is the fact, the color is the interpretation.
    """

    if current is None or previous is None or previous == 0:
        # ONE dash, not `compare`'s `- -` arrow/pct pair: there is a single
        # thing missing here, and two dashes in a row read as a typo.
        return Cell(ARROW_NONE, DIM)

    pct = (current - previous) / previous * 100.0
    if pct == 0:
        return Cell(f"{ARROW_FLAT} +0.0%", DIM)

    rose = pct > 0
    arrow = ARROW_UP if rose else ARROW_DOWN
    sign = "+" if rose else ""
    better = rose if default_higher_is_better(metric_name) else not rose
    return Cell(f"{arrow} {sign}{pct:.1f}%", BOLD_GREEN if better else BOLD_RED)


def _chart_section(runs: Sequence[HistoryRun], metric_name: str) -> list[str]:
    """The y-axis chart of p90 over `runs`, behind this view's rail. Runs
    without a p90 are absent from BOTH the bars and the labels, so every label
    still sits under its own bar."""

    # Built as (label, value) PAIRS rather than two comprehensions over the
    # same filter: pairing them here is what guarantees the Nth label belongs
    # to the Nth bar, and it is also the only shape in which the `p90 is not
    # None` narrowing survives to the `chart_lines` call.
    charted: list[tuple[str, float]] = []
    for run in runs:
        metric = _metric_in_run(run, metric_name)
        if metric is None or metric.p90 is None:
            continue
        charted.append((_chart_label(run), metric.p90))

    if not charted:
        return ["│   (no chart — no p90 recorded for this metric in this window)"]

    lines = chart_lines(
        [value for _, value in charted],
        [label for label, _ in charted],
    )
    return [f"│   {line}" for line in lines]


def _table_section(runs: Sequence[HistoryRun], metric_name: str, *, color: bool) -> list[str]:
    lines = [f"│   {_HEADER_LINE}", f"│   {'─' * _RULE_WIDTH}"]

    previous: float | None = None
    for run in runs:
        metric = _metric_in_run(run, metric_name)
        p90 = metric.p90 if metric is not None else None
        lines.append(
            "│   "
            + table_line(
                [
                    str(run.run_id),
                    _date_part(run.started_at),
                    _short_commit(run.git_commit),
                    format_value(metric.p50 if metric is not None else None),
                    format_value(p90),
                    _delta_cell(metric_name, p90, previous),
                ],
                _HISTORY_COLUMNS,
                color=color,
            )
        )
        if p90 is not None:
            previous = p90
    return lines


def _metric_section(
    runs: Sequence[HistoryRun], metric_name: str, *, total_runs: int, color: bool
) -> list[str]:
    """One metric: a header line carrying the WHOLE window as a sparkline, then
    the chart and the table over the most recent `_MAX_TABLE_ROWS` runs."""

    unit = _unit_for(runs, metric_name)
    window = _p90_series(runs, metric_name)
    recent = runs[-_MAX_TABLE_ROWS:]

    header = style(f"{metric_name} ({unit})", color=color, code=BOLD)
    window_bits = [f"window {sparkline(window)}"] if window else []
    if len(recent) < total_runs:
        window_bits.append(f"charting the last {len(recent)} of {total_runs} runs")
    trailer = style("  ·  ".join(window_bits), color=color, code=DIM) if window_bits else ""

    lines = [f"│   {header}   {trailer}".rstrip()]
    lines.append("│")
    lines.extend(_chart_section(recent, metric_name))
    lines.append("│")
    lines.extend(_table_section(recent, metric_name, color=color))
    return lines


def render_history(
    flow: str, device: str, mode: str, runs: Sequence[HistoryRun], *, color: bool = False
) -> str:
    """Render the historical series for `flow` (already OLDEST→NEWEST and
    already restricted to the requested metric when `--metric` was passed)
    inside an open-right box: a `┌─` header naming WHAT was charted, then one
    section per metric.

    Honors `color=False` (the CLI resolves this from `--no-color`/`NO_COLOR`/
    non-TTY via the shared `OutputContext`) by emitting NO ANSI escapes at
    all."""

    lines: list[str] = [
        f"┌─ perfvibe history · {flow} · {mode} · {device_label(device)} · {len(runs)} run(s)",
        "│",
    ]
    for metric_name in _metric_names(runs):
        lines.extend(_metric_section(runs, metric_name, total_runs=len(runs), color=color))
        lines.append("│")
    lines.append("└─")
    return "\n".join(lines) + "\n"
