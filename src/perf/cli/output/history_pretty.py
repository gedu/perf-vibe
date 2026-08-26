"""Pretty reporter for `perf history <flow>` — human-readable, LOSSY (it
summarizes and truncates the run table; it must NEVER be parsed — SKILL
rule 6; the `--json` `history_v1` payload is the machine contract). One
compact section per metric: a header (name + unit) with a sparkline of p90
over the runs, then a short table of the most recent runs (run id, date,
short commit, p50/p90). Color/TTY-aware via the caller-resolved `color`
flag (golden tests force it off; the CLI resolves it via the shared
`OutputContext`, mirroring `cli/output/compare_pretty.py`).

Draws with the shared `output/primitives.py` vocabulary rather than
re-implementing the Unicode block-char scaling — the SAME sparkline the
`compare` verdict view draws, now owned by neither renderer.
"""

from __future__ import annotations

from collections.abc import Sequence

from perf.cli.output.primitives import BOLD, DIM, format_value, sparkline, style
from perf.domain.model import HistoryMetric, HistoryRun

__all__ = ["render_history"]

# The most recent runs shown in each metric's table (the sparkline still
# spans the WHOLE queried window — the table is the lossy, readable excerpt).
_MAX_TABLE_ROWS = 8


def _short_commit(commit: str | None) -> str:
    """Stays local rather than joining `output/primitives.py`: it looks like
    `budget_check_pretty._short_sha` but its MISSING-value fallback differs
    (`-` in this table, `unknown` in budget-check's header). The fallback is
    the user-visible half, so these are two different renderings that happen
    to share a `[:7]`, not one shared primitive."""

    return "-" if not commit else commit[:7]


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


def _metric_section(runs: Sequence[HistoryRun], metric_name: str, *, color: bool) -> list[str]:
    unit = _unit_for(runs, metric_name)
    p90_series = [
        metric.p90
        for run in runs
        if (metric := _metric_in_run(run, metric_name)) is not None and metric.p90 is not None
    ]
    spark = sparkline(p90_series)
    header = style(f"{metric_name} ({unit})", color=color, code=BOLD)

    lines = [f"{header}  {spark}".rstrip()]
    lines.append(
        style(
            f"  {'run':<8} {'date':<12} {'commit':<9} {'p50':>10} {'p90':>10}",
            color=color,
            code=DIM,
        )
    )
    for run in runs[-_MAX_TABLE_ROWS:]:
        metric = _metric_in_run(run, metric_name)
        p50 = format_value(metric.p50) if metric is not None else "-"
        p90 = format_value(metric.p90) if metric is not None else "-"
        lines.append(
            f"  {run.run_id:<8} {_date_part(run.started_at):<12} "
            f"{_short_commit(run.git_commit):<9} {p50:>10} {p90:>10}"
        )
    return lines


def render_history(
    flow: str, device: str, mode: str, runs: Sequence[HistoryRun], *, color: bool = False
) -> str:
    """Render the historical series for `flow` (already OLDEST→NEWEST and
    already restricted to the requested metric when `--metric` was passed).
    One section per metric; honors `color=False` by emitting NO ANSI escapes
    at all (the CLI resolves this from `--no-color`/`NO_COLOR`/non-TTY via
    the shared `OutputContext`)."""

    heading = style(
        f"{flow} — device={device} mode={mode} — {len(runs)} run(s)",
        color=color,
        code=BOLD,
    )
    lines = [heading, ""]
    for metric_name in _metric_names(runs):
        lines.extend(_metric_section(runs, metric_name, color=color))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
