"""Pretty verdict reporter for `perf compare` — human-readable, LOSSY (it
summarizes; it must NEVER be parsed — SKILL rule 6). Per-metric line +
sparkline (design "UX", Rev 3), plus a single config-sanity footer line
(decision #58). Color/TTY-aware via the caller-resolved `color` flag
(golden tests force it off; the CLI resolves it via the shared
`OutputContext`, mirroring `cli/output/pretty.py`'s `render_confirmation`).
"""

from __future__ import annotations

from typing import Sequence

from perf.domain import calibration, regression
from perf.domain.calibration import CalibrationReport
from perf.domain.model import CompareResult, Verdict

__all__ = ["render_compare"]

_BOLD_RED = "\x1b[1;31m"
_RESET = "\x1b[0m"

# Stdlib Unicode block characters, low -> high (design "UX": "▁▂▃▅▇").
_SPARK_CHARS = "▁▂▃▄▅▆▇█"

_ARROW_UP = "↑"
_ARROW_DOWN = "↓"
_ARROW_FLAT = "→"
_ARROW_NONE = "-"


def _style(text: str, *, color: bool, code: str) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _sparkline(series: Sequence[float]) -> str:
    """Normalizes `series` to its own min/max and maps each point to one
    of the 8 block-char levels. Handles empty, single-point, and
    `max == min` (zero variance) without a divide-by-zero (spec
    'Pretty-Output UX' sparkline edges)."""

    if not series:
        return ""
    if len(series) == 1:
        return _SPARK_CHARS[0]

    lo, hi = min(series), max(series)
    span = hi - lo
    if span == 0:
        # Zero-variance series — render the flat middle level for every
        # point rather than dividing by zero.
        flat = _SPARK_CHARS[len(_SPARK_CHARS) // 2]
        return flat * len(series)

    top_index = len(_SPARK_CHARS) - 1
    return "".join(_SPARK_CHARS[round((value - lo) / span * top_index)] for value in series)


def _format_value(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _arrow_and_pct(verdict: Verdict) -> tuple[str, str]:
    if verdict.status == regression.STATUS_INSUFFICIENT_DATA:
        return _ARROW_NONE, "-"
    delta_pct = verdict.delta_pct
    arrow = _ARROW_UP if delta_pct > 0 else _ARROW_DOWN if delta_pct < 0 else _ARROW_FLAT
    sign = "+" if delta_pct >= 0 else ""
    return arrow, f"{sign}{delta_pct:.1f}%"


def _metric_line(verdict: Verdict, *, color: bool) -> str:
    latest = _format_value(verdict.latest_value)
    baseline = _format_value(verdict.baseline_value)
    arrow, pct = _arrow_and_pct(verdict)
    sparkline = _sparkline(verdict.series)
    classification = verdict.status.upper()

    is_regression = verdict.status == regression.STATUS_REGRESSION
    marker = "! " if is_regression else "  "
    text = (
        f"{marker}{verdict.metric_name:<20} {latest:>10} vs {baseline:<10} {verdict.unit:<4} "
        f"{arrow} {pct:>8}  {classification:<16} {sparkline}"
    )
    if is_regression:
        # Color path bolds/reddens; color-off path keeps the leading "!"
        # and the "REGRESSION" word — emphasis never depends on color
        # alone (spec 'Regression is visually emphasized').
        return _style(text, color=color, code=_BOLD_RED)
    return text


def _sanity_label(report: CalibrationReport) -> str:
    if report.status == calibration.STATUS_REASONABLE:
        return f"✓ reasonable — {report.runs_flagged} of {report.runs_total} runs would flag"
    if report.status == calibration.STATUS_TOO_LOOSE:
        return "⚠ too loose — config may miss real regressions (floor exceeds observed deltas)"
    if report.status == calibration.STATUS_TOO_STRICT:
        return "⚠ too strict — normal noise may look like a regression"
    return "· insufficient data to grade config sanity"


def render_compare(result: CompareResult, *, color: bool = False) -> str:
    """Per-metric line (name, latest vs baseline, arrow + signed %,
    classification, sparkline) followed by ONE sanity-label footer line
    (design "UX" — never interleaved mid-metric). Honors `color=False`
    (the CLI resolves this from `--no-color`/`NO_COLOR`/non-TTY via the
    shared `OutputContext`) by emitting NO ANSI escapes at all."""

    lines: list[str] = [_metric_line(verdict, color=color) for verdict in result.verdicts]
    lines.append("")
    lines.append(_sanity_label(result.calibration))
    return "\n".join(lines) + "\n"
