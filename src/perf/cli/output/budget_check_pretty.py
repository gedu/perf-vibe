"""Pretty renderer for `perf budget-check` — budget-check's OWN view (design
§9, decision D2). `compare_pretty.py` stays FROZEN and is NEVER imported
here; a small amount of duplication (sparkline normalization, arrow/pct
formatting) is deliberate rather than coupling two renderers together.

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

from perf.domain import calibration, regression
from perf.domain.calibration import CalibrationReport
from perf.domain.model import (
    GATE_FAIL,
    GATE_PASS,
    BudgetVerdict,
    GatedVerdict,
    RunContext,
    SeriesPoint,
    Verdict,
    default_higher_is_better,
)
from perf.domain.ports import CommitLog

__all__ = ["render_metric_detail", "render_summary"]

_BOLD_RED = "\x1b[1;31m"
_GREEN = "\x1b[1;32m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"

_SPARK_CHARS = "▁▂▃▄▅▆▇█"

_ARROW_UP = "↑"
_ARROW_DOWN = "↓"
_ARROW_FLAT = "→"
_ARROW_NONE = "-"

_GLYPH_OFFENDER = "✗"
_GLYPH_OK = "✓"
_GLYPH_NEUTRAL = "·"

_RULE_WIDTH = 74
_NAME_W = 15
_VALUE_W = 12
_DELTA_W = 9
_STATUS_W = 20

_CHART_ROWS = 5
_COL_W = 8
_PREFIX_W = 10  # "{value:>7.1f} ┤ " — 7 + 3 chars


def _style(text: str, *, color: bool, code: str) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _short_sha(sha: str | None) -> str:
    if not sha:
        return "unknown"
    return sha[:7]


def _sparkline(series: Sequence[float]) -> str:
    """Same normalization guard as `compare_pretty._sparkline` (design risk
    #3 requires the SAME discipline here, deliberately re-implemented
    rather than imported so this renderer has no coupling to the frozen
    `compare_pretty` module): empty/single-point/zero-variance never
    divide by zero."""

    if not series:
        return ""
    if len(series) == 1:
        return _SPARK_CHARS[0]
    lo, hi = min(series), max(series)
    span = hi - lo
    if span == 0:
        return _SPARK_CHARS[len(_SPARK_CHARS) // 2] * len(series)
    top_index = len(_SPARK_CHARS) - 1
    return "".join(_SPARK_CHARS[round((value - lo) / span * top_index)] for value in series)


def _format_value(value: float | None, unit: str) -> str:
    return "-" if value is None else f"{value:.1f} {unit}"


def _arrow_and_pct(verdict: Verdict) -> tuple[str, str]:
    if verdict.status == regression.STATUS_INSUFFICIENT_DATA:
        return _ARROW_NONE, "-"
    delta_pct = verdict.delta_pct
    arrow = _ARROW_UP if delta_pct > 0 else _ARROW_DOWN if delta_pct < 0 else _ARROW_FLAT
    sign = "+" if delta_pct >= 0 else ""
    return arrow, f"{sign}{delta_pct:.1f}%"


def _row_glyph(gv: GatedVerdict) -> str:
    if gv.gated:
        return _GLYPH_OFFENDER
    if gv.verdict.status == regression.STATUS_INSUFFICIENT_DATA:
        return _GLYPH_NEUTRAL
    return _GLYPH_OK


def _metric_row(gv: GatedVerdict, *, color: bool) -> str:
    verdict = gv.verdict
    latest = _format_value(verdict.latest_value, verdict.unit)
    baseline = _format_value(verdict.baseline_value, verdict.unit)
    arrow, pct = _arrow_and_pct(verdict)
    sparkline = _sparkline(verdict.series)
    glyph = _row_glyph(gv)
    status_word = verdict.status.upper() if gv.gated else verdict.status.lower()

    text = (
        f"│   {glyph} {verdict.metric_name:<{_NAME_W}}{latest:>{_VALUE_W}}{baseline:>{_VALUE_W}}"
        f"   {arrow} {pct:>{_DELTA_W}}  {status_word:<{_STATUS_W}}{sparkline}"
    )
    if gv.gated:
        return _style(text, color=color, code=_BOLD_RED)
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
        return f"{_GLYPH_OK}  GATE PASSED   ·   0 regressions   ·   exit 0"
    if bv.gate_status != GATE_FAIL:
        return (
            f"{_GLYPH_NEUTRAL}  GATE SKIPPED   ·   not enough history to judge "
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
    return f"{_GLYPH_OFFENDER}  GATE FAILED   ·   {detail}   ·   exit 1"


def _gate_footer_color(bv: BudgetVerdict) -> str | None:
    if bv.gate_status == GATE_FAIL:
        return _BOLD_RED
    if bv.gate_status == GATE_PASS:
        return _GREEN
    return _DIM


def _expand_regressed_row(gv: GatedVerdict, subject: str | None) -> str:
    verdict = gv.verdict
    arrow, pct = _arrow_and_pct(verdict)
    latest = _format_value(verdict.latest_value, verdict.unit)
    baseline = _format_value(verdict.baseline_value, verdict.unit)
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
    header = (
        f"│   {'METRIC':<{_NAME_W}}{'LATEST':>{_VALUE_W}}{'BASELINE':>{_VALUE_W}}"
        f"   {'Δ':>{_DELTA_W + 2}}  {'STATUS':<{_STATUS_W}}TREND"
    )
    lines.append(header)
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
    lines.append(f"├{'─' * (width + 1)}")
    lines.append("│")
    lines.append(f"│   {_style(_gate_footer(bv), color=color, code=_gate_footer_color(bv) or '')}")
    lines.append("│")
    lines.append("└─")
    return "\n".join(lines) + "\n"


def _select_gated_verdict(bv: BudgetVerdict, metric_name: str) -> GatedVerdict | None:
    for gv in bv.gated_verdicts:
        if gv.verdict.metric_name == metric_name:
            return gv
    return None


def _y_ticks(values: Sequence[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [lo]
    return [hi - (i / (_CHART_ROWS - 1)) * (hi - lo) for i in range(_CHART_ROWS)]


def _render_chart(points: Sequence[SeriesPoint], head_commit: str | None) -> list[str]:
    if not points:
        return ["│   (no chart data — empty series)"]

    values = [p.value for p in points]
    ticks = _y_ticks(values)
    lines: list[str] = []
    for threshold in ticks:
        cells = "".join(f"{'██' if v >= threshold - 1e-9 else '':<{_COL_W}}" for v in values)
        lines.append(f"│   {threshold:>7.1f} ┤ {cells}".rstrip())

    axis = "│   " + " " * _PREFIX_W + "└" + "─" * (_COL_W * len(points))
    lines.append(axis.rstrip())

    labels = "│   " + " " * _PREFIX_W + "".join(f"{_short_sha(p.commit):<{_COL_W}}" for p in points)
    lines.append(labels.rstrip())

    head_idx = None
    if head_commit:
        for idx, point in enumerate(points):
            if point.commit == head_commit:
                head_idx = idx
    if head_idx is not None:
        marker = "│   " + " " * (_PREFIX_W + _COL_W * head_idx) + "└ HEAD"
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

    latest_line = f"│   latest     {_format_value(verdict.latest_value, verdict.unit):<10}"
    if is_regression:
        head_bit = f'"{subject}"' if subject else "(subject unavailable)"
        latest_line += f" at HEAD  {_short_sha(rc.git_commit)}  {head_bit}"
    lines.append(latest_line)
    lines.append(
        f"│   baseline   {_format_value(verdict.baseline_value, verdict.unit):<10} "
        f"median of {verdict.baseline_commit_n} commits"
    )
    arrow, pct = _arrow_and_pct(verdict)
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
