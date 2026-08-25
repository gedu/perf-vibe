"""Pretty verdict reporter for `perf compare` — human-readable, LOSSY (it
summarizes; it must NEVER be parsed — SKILL rule 6). Per-metric line +
sparkline (design "UX", Rev 3), plus a single config-sanity footer line
(decision #58). Color/TTY-aware via the caller-resolved `color` flag
(golden tests force it off; the CLI resolves it via the shared
`OutputContext`, mirroring `cli/output/pretty.py`'s `render_confirmation`).
"""

from __future__ import annotations

from perf.cli.output.primitives import (
    BOLD,
    BOLD_RED,
    DIM,
    arrow_and_pct,
    format_value,
    sparkline,
    style,
)
from perf.domain import calibration, regression
from perf.domain.calibration import CalibrationReport
from perf.domain.model import CompareResult, Verdict

__all__ = ["render_compare", "render_flow_header"]


def _metric_line(verdict: Verdict, *, color: bool) -> str:
    latest = format_value(verdict.latest_value)
    baseline = format_value(verdict.baseline_value)
    arrow, pct = arrow_and_pct(verdict)
    spark = sparkline(verdict.series)
    classification = verdict.status.upper()

    is_regression = verdict.status == regression.STATUS_REGRESSION
    marker = "! " if is_regression else "  "
    text = (
        f"{marker}{verdict.metric_name:<20} {latest:>10} vs {baseline:<10} {verdict.unit:<4} "
        f"{arrow} {pct:>8}  {classification:<16} {spark}"
    )
    if is_regression:
        # Color path bolds/reddens; color-off path keeps the leading "!"
        # and the "REGRESSION" word — emphasis never depends on color
        # alone (spec 'Regression is visually emphasized').
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


def _excluded_note(result: CompareResult, *, color: bool) -> str | None:
    """ONE dim explanatory line (anti-false-positive batch, Task 4) shown
    ONLY when the baseline query silently dropped runs — so a dev iterating
    on a single sha (or on an uncommitted tree) understands WHY history looks
    thin, instead of a bare "insufficient data". Lossy pretty-only; the
    `--json` payload never carries these counts (contract unchanged). Returns
    `None` when nothing was excluded, so the common case adds no line."""

    same_commit = result.excluded_same_commit
    no_commit = result.excluded_no_commit
    total = same_commit + no_commit
    if total <= 0:
        return None

    clauses: list[str] = []
    if same_commit > 0:
        clauses.append(f"{same_commit} on the current commit")
    if no_commit > 0:
        clauses.append(f"{no_commit} without a git commit")
    detail = ", ".join(clauses)
    text = (
        f"note: {total} run(s) excluded from baseline: {detail} "
        "— commit your changes to grow history"
    )
    return style(text, color=color, code=DIM)


def render_flow_header(flow_name: str, *, color: bool = False) -> str:
    """A single, clear per-flow header line for the MULTI-flow `compare`
    view (2+ flows or `--all`), so sequentially rendered flows never blur
    together. Bolded when color is on; plain (byte-clean) when off. Never
    emitted for a single-flow compare — that output stays byte-identical."""

    return style(f"═══ {flow_name} ═══", color=color, code=BOLD)


def render_compare(result: CompareResult, *, color: bool = False) -> str:
    """Per-metric line (name, latest vs baseline, arrow + signed %,
    classification, sparkline) followed by ONE sanity-label footer line
    (design "UX" — never interleaved mid-metric). Honors `color=False`
    (the CLI resolves this from `--no-color`/`NO_COLOR`/non-TTY via the
    shared `OutputContext`) by emitting NO ANSI escapes at all."""

    lines: list[str] = [_metric_line(verdict, color=color) for verdict in result.verdicts]
    lines.append("")
    lines.append(_sanity_label(result.calibration))
    note = _excluded_note(result, color=color)
    if note is not None:
        lines.append(note)
    return "\n".join(lines) + "\n"
