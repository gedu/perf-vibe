"""Pretty renderer for `perf budget-check` — budget-check's OWN view (design
§9, decision D2). `compare_pretty.py` is still NEVER imported here. The
shared vocabulary (sparkline normalization, arrow/pct formatting, ANSI codes,
glyphs, and the `table_line`/`header_line` layout) comes from
`output/primitives.py`, which keeps the original no-coupling guarantee
WITHOUT the duplication that used to buy it: this renderer depends on a
primitive owned by no view, not on another renderer. `compare` now draws the
same table, but from its OWN column spec — the two views share mechanics, not
layout decisions.

HAND-ROLLED, NOT `rich` (design §9 rationale: determinism is free
hand-rolled — pass an explicit `color: bool`, emit zero ANSI when false,
render at a fixed width; the layout is trivial box-drawing that does not
justify a table/tree engine).

Layout law: OPEN-RIGHT. Top rule, bottom rule, and a left rail `│` only —
NEVER a right border (wide sparkline/block glyphs desync monospace
alignment; leaving the right open avoids ragged edges, spec 'Pretty Output
(Own Renderer)'). A blank rail line separates metric rows so sparklines
never collide vertically.

Emphasis never depends on color alone: every gated/regressed row and the
gate banner carry BOTH a glyph (`✗`/`✓`/`·`) AND the STATUS word (uppercase
when the metric is a gate offender, lowercase otherwise) — legible with
`color=False` and no ANSI escapes at all.
"""

from __future__ import annotations

from collections.abc import Sequence

from perf.cli.output.primitives import (
    BOLD_GREEN,
    BOLD_RED,
    CHART_PREFIX_W,
    DIM,
    GLYPH_NEUTRAL,
    GLYPH_OFFENDER,
    GLYPH_OK,
    ColumnSpec,
    arrow_and_pct,
    chart_lines,
    format_value,
    header_line,
    sparkline,
    style,
    table_line,
)
from perf.domain import calibration, regression
from perf.domain.calibration import CalibrationReport
from perf.domain.model import (
    GATE_FAIL,
    GATE_PASS,
    BudgetVerdict,
    GatedVerdict,
    RunContext,
    SeriesPoint,
    default_higher_is_better,
)
from perf.domain.ports import CommitLog

__all__ = ["render_metric_detail", "render_summary"]

# ONE column spec drives BOTH the header and every data row, laid out by the
# shared `primitives.table_line`. This view still OWNS the spec — only the
# layout mechanics are shared, now that `compare_pretty` is a second real
# caller with the same shape (`python-architecture` rule 3).
# (title, width, alignment). Width 0 marks the flexible trailing column.
_SUMMARY_COLUMNS: tuple[ColumnSpec, ...] = (
    ("", 1, "<"),  # status glyph — a real column, so the header cannot drift past it
    ("METRIC", 14, "<"),
    ("LATEST", 11, ">"),
    ("BASELINE", 11, ">"),
    ("Δ", 9, ">"),
    ("STATUS", 17, "<"),
    ("TREND", 0, "<"),
)

# This view's chart sizing, passed EXPLICITLY to the shared `chart_lines` — the
# gutter width is not here, because it is derived from the primitive's own tick
# format and the `└ HEAD` marker below must move with it, not with a copy.
_CHART_ROWS = 5
_COL_W = 8

# Derived, never hand-counted: the rules span exactly the table they underline,
# so widening a column cannot leave a rule short (the earlier version hardcoded
# 74 and the two rules rendered 78 and 76 characters wide).
_HEADER_LINE = header_line(_SUMMARY_COLUMNS)
_RULE_WIDTH = len(_HEADER_LINE)


def _short_sha(sha: str | None) -> str:
    """Stays local rather than joining `output/primitives.py`: it looks like
    `history_pretty._short_commit` but its MISSING-value fallback differs
    (`unknown` in this header, `-` in history's table). The fallback is the
    user-visible half, so these are two different renderings that happen to
    share a `[:7]`, not one shared primitive."""

    if not sha:
        return "unknown"
    return sha[:7]


def _row_glyph(gv: GatedVerdict) -> str:
    if gv.gated:
        return GLYPH_OFFENDER
    if gv.verdict.status == regression.STATUS_INSUFFICIENT_DATA:
        return GLYPH_NEUTRAL
    return GLYPH_OK


def _metric_row(gv: GatedVerdict, *, color: bool) -> str:
    verdict = gv.verdict
    latest = format_value(verdict.latest_value, verdict.unit)
    baseline = format_value(verdict.baseline_value, verdict.unit)
    arrow, pct = arrow_and_pct(verdict)
    spark = sparkline(verdict.series)
    glyph = _row_glyph(gv)
    status_word = verdict.status.upper() if gv.gated else verdict.status.lower()

    text = "│   " + table_line(
        [glyph, verdict.metric_name, latest, baseline, f"{arrow} {pct}", status_word, spark],
        _SUMMARY_COLUMNS,
    )
    if gv.gated:
        return style(text, color=color, code=BOLD_RED)
    return text


def _sanity_label(report: CalibrationReport) -> str:
    if report.status == calibration.STATUS_REASONABLE:
        return f"✓ reasonable — {report.runs_flagged} of {report.runs_total} runs would flag"
    if report.status == calibration.STATUS_TOO_LOOSE:
        return "⚠ too loose — floor suppressed a change your threshold would flag"
    if report.status == calibration.STATUS_TOO_STRICT:
        return "⚠ too strict — normal noise may look like a regression"
    return "· insufficient data to grade config sanity"


def _gate_footer(bv: BudgetVerdict) -> str:
    if bv.gate_status == GATE_PASS:
        return f"{GLYPH_OK}  GATE PASSED   ·   0 regressions   ·   exit 0"
    if bv.gate_status != GATE_FAIL:
        return (
            f"{GLYPH_NEUTRAL}  GATE SKIPPED   ·   not enough history to judge "
            "(fail-open)   ·   exit 0"
        )

    regressed = sum(
        1
        for gv in bv.gated_verdicts
        if gv.gated and gv.verdict.status == regression.STATUS_REGRESSION
    )
    insufficient = len(bv.offending_metrics) - regressed
    parts = []
    if regressed:
        parts.append(f"{regressed} metric{'s' if regressed != 1 else ''} regressed")
    if insufficient:
        parts.append(f"{insufficient} insufficient-data (--strict)")
    detail = "   ·   ".join(parts) if parts else f"{len(bv.offending_metrics)} metric(s) gated"
    return f"{GLYPH_OFFENDER}  GATE FAILED   ·   {detail}   ·   exit 1"


def _gate_footer_color(bv: BudgetVerdict) -> str | None:
    if bv.gate_status == GATE_FAIL:
        return BOLD_RED
    if bv.gate_status == GATE_PASS:
        return BOLD_GREEN
    return DIM


def _expand_regressed_row(gv: GatedVerdict, subject: str | None) -> str:
    verdict = gv.verdict
    arrow, pct = arrow_and_pct(verdict)
    latest = format_value(verdict.latest_value, verdict.unit)
    baseline = format_value(verdict.baseline_value, verdict.unit)
    head_bit = f'"{subject}"' if subject else "(subject unavailable)"
    return f"│       └─ baseline {baseline} · latest {latest} · Δ {arrow} {pct} · HEAD {head_bit}"


def render_summary(
    bv: BudgetVerdict,
    rc: RunContext,
    commit_log: CommitLog,
    *,
    flow_name: str,
    verbose: bool = False,
    color: bool = False,
    width: int = _RULE_WIDTH,
) -> str:
    """Per-metric summary (design §9): ALL metrics shown (not only
    offenders), a sparkline each, the calibration footer, and a gate
    banner. `--verbose` auto-expands each REGRESSED metric inline,
    fetching `commit_log.subject(rc.git_commit)` ONCE and reusing it
    across every expanded row (design risk: 'exactly one `git log` call
    per invocation' — task 3.3)."""

    head = _short_sha(rc.git_commit)
    branch = rc.git_branch or "unknown"
    lines: list[str] = [f"┌─ perfvibe budget-check · {flow_name} · HEAD {head} ({branch})"]
    lines.append("│")
    lines.append(f"│   {_HEADER_LINE}")
    lines.append(f"│   {'─' * width}")
    lines.append("│")

    regressed = [
        gv for gv in bv.gated_verdicts if gv.verdict.status == regression.STATUS_REGRESSION
    ]
    subject: str | None = None
    if verbose and regressed:
        subject = commit_log.subject(rc.git_commit) if rc.git_commit else None

    for gv in bv.gated_verdicts:
        lines.append(_metric_row(gv, color=color))
        if verbose and gv.verdict.status == regression.STATUS_REGRESSION:
            lines.append(_expand_regressed_row(gv, subject))
        lines.append("│")

    lines.append(f"│   {_sanity_label(bv.calibration)}")
    lines.append("│")
    # +3 so the divider reaches the same right edge as the table above it
    # (the content is indented 4 by the "│   " rail).
    lines.append(f"├{'─' * (width + 3)}")
    lines.append("│")
    lines.append(f"│   {style(_gate_footer(bv), color=color, code=_gate_footer_color(bv) or '')}")
    lines.append("│")
    lines.append("└─")
    return "\n".join(lines) + "\n"


def _select_gated_verdict(bv: BudgetVerdict, metric_name: str) -> GatedVerdict | None:
    for gv in bv.gated_verdicts:
        if gv.verdict.metric_name == metric_name:
            return gv
    return None


def _render_chart(points: Sequence[SeriesPoint], head_commit: str | None) -> list[str]:
    """This view's chart: the shared `primitives.chart_lines` behind its own
    box rail, plus the two things that are budget-check's alone — the
    empty-series wording, and the `└ HEAD` marker under the run being gated."""

    if not points:
        return ["│   (no chart data — empty series)"]

    lines = [
        f"│   {line}"
        for line in chart_lines(
            [p.value for p in points],
            [_short_sha(p.commit) for p in points],
            rows=_CHART_ROWS,
            col_w=_COL_W,
        )
    ]

    head_idx = None
    if head_commit:
        for idx, point in enumerate(points):
            if point.commit == head_commit:
                head_idx = idx
    if head_idx is not None:
        marker = "│   " + " " * (CHART_PREFIX_W + _COL_W * head_idx) + "└ HEAD"
        lines.append(marker)

    return lines


def render_metric_detail(
    bv: BudgetVerdict,
    metric_name: str,
    rc: RunContext,
    commit_log: CommitLog,
    *,
    flow_name: str,
    mode: str,
    color: bool = False,
    width: int = _RULE_WIDTH,
) -> str:
    """Single-metric drill-down (design §9): y-axis value ticks, x-axis
    per-commit short-sha labels, HEAD marked; git context (sha, branch,
    commit subject) on a `regression`, fail-graceful to sha-only when the
    subject is unavailable (never crashes — spec 'Git Context on
    Regression'). A metric absent from this run, or present with no data
    this run (`latest_value is None`), renders a clear message and still
    exit-maps by the OVERALL `gate_status` (never a usage error — tasks
    3.14/3.15)."""

    gv = _select_gated_verdict(bv, metric_name)
    if gv is None:
        missing_lines = [
            f"┌─ {flow_name} · {metric_name} not in this run",
            "│",
            f"│   metric {metric_name!r} has no data for flow {flow_name!r} in this run.",
            "│",
            "└─",
        ]
        return "\n".join(missing_lines) + "\n"

    verdict = gv.verdict
    direction = "higher-is-better" if default_higher_is_better(metric_name) else "lower-is-better"
    status_word = verdict.status.upper()
    device_label = rc.model or "unknown device"

    lines: list[str] = [f"┌─ {flow_name} · {status_word} · {direction} · {mode} · {device_label}"]
    lines.append("│")

    if verdict.latest_value is None:
        lines.append(f"│   no data for metric {metric_name!r} in this run.")
        lines.append("│")
        lines.append("└─")
        return "\n".join(lines) + "\n"

    is_regression = verdict.status == regression.STATUS_REGRESSION
    subject: str | None = None
    if is_regression and rc.git_commit:
        subject = commit_log.subject(rc.git_commit)

    latest_line = f"│   latest     {format_value(verdict.latest_value, verdict.unit):<10}"
    if is_regression:
        head_bit = f'"{subject}"' if subject else "(subject unavailable)"
        latest_line += f" at HEAD  {_short_sha(rc.git_commit)}  {head_bit}"
    lines.append(latest_line)
    lines.append(
        f"│   baseline   {format_value(verdict.baseline_value, verdict.unit):<10} "
        f"median of {verdict.baseline_commit_n} commits"
    )
    arrow, pct = arrow_and_pct(verdict)
    breach_word = "BREACHED" if gv.gated else "within bounds"
    lines.append(
        f"│   delta      {arrow} {pct:<10} threshold {verdict.threshold_pct:.1f}%  ·  "
        f"floor {verdict.floor:.1f} {verdict.unit}  ·  {breach_word}"
    )
    lines.append("│")
    lines.append(f"│   {verdict.unit}")

    head_commit = rc.git_commit
    for chart_line in _render_chart(verdict.series_points, head_commit):
        lines.append(chart_line)
    lines.append("│")

    baseline_points = [p for p in verdict.series_points if p.commit != head_commit]
    if baseline_points:
        lines.append("│   baseline commits (median-by-commit):")
        row: list[str] = []
        for point in baseline_points:
            row.append(f"{_short_sha(point.commit):>7} {point.value:>6.1f}")
            if len(row) == 4:
                lines.append("│     " + "    ".join(row))
                row = []
        if row:
            lines.append("│     " + "    ".join(row))
        if any(p.commit == head_commit for p in verdict.series_points) and head_commit:
            lines.append(f"│     (HEAD {_short_sha(head_commit)} excluded)")
        lines.append("│")

    lines.append("└─")
    return "\n".join(lines) + "\n"
