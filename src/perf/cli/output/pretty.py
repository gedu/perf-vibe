"""Pretty confirmation reporter for `perf run` — human-readable, LOSSY (it
summarizes; it must NEVER be parsed — SKILL rule 6). Color/TTY-aware via
the caller-resolved `color` flag (golden tests force it off)."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence

from perf.application.run_flow import RunFlowResult
from perf.domain.model import Marker, SystemSample

__all__ = ["render_confirmation"]

_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RESET = "\x1b[0m"


def _style(text: str, *, color: bool, code: str) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _grouped_markers(markers: Sequence[Marker]) -> Mapping[str, list]:
    grouped: dict[str, list] = {}
    for marker in markers:
        grouped.setdefault(marker.name, []).append(marker.value)
    return grouped


def render_confirmation(result: RunFlowResult, *, color: bool = False) -> str:
    lines: list[str] = []
    lines.append(_style(f"✓ perf run complete — run #{result.run_id}", color=color, code=_GREEN))
    lines.append(f"  flow:       {result.flow_name}")
    lines.append(f"  device:     {result.device_key}")
    lines.append(f"  mode:       {result.mode} (n={result.iterations})")
    lines.append(f"  source:     {result.source}")
    lines.append(f"  commit:     {result.git_commit or '-'}")
    dev_bundle = (
        "unknown" if result.is_dev_bundle is None else ("yes" if result.is_dev_bundle else "no")
    )
    lines.append(f"  dev bundle: {dev_bundle}")

    if result.partial_coverage:
        lines.append(
            _style(
                "  ! partial coverage — some iterations were missing data",
                color=color,
                code=_YELLOW,
            )
        )

    grouped = _grouped_markers(result.markers)
    if grouped:
        lines.append("")
        lines.append("  markers:")
        for name in sorted(grouped):
            values = grouped[name]
            avg = statistics.fmean(values)
            lines.append(f"    {name}: n={len(values)} avg={avg:.1f}ms")

    if result.samples:
        lines.append("")
        lines.append("  flashlight (per-iteration aggregates):")
        lines.append(f"    iterations captured: {len(result.samples)}")
        _append_aggregate_line(lines, result.samples, "fps_avg", "fps avg", "{:.1f}")
        _append_aggregate_line(lines, result.samples, "ram_peak_mb", "ram peak", "{:.1f}MB")
        _append_aggregate_line(lines, result.samples, "cpu_avg_pct", "cpu avg", "{:.1f}%")

    if result.raw_report_path:
        lines.append("")
        lines.append(f"  raw report: {result.raw_report_path}")

    return "\n".join(lines) + "\n"


def _append_aggregate_line(
    lines: list[str],
    samples: Sequence[SystemSample],
    field_name: str,
    label: str,
    fmt: str,
) -> None:
    values = [
        getattr(sample, field_name) for sample in samples if getattr(sample, field_name) is not None
    ]
    if values:
        lines.append(f"    {label}: {fmt.format(statistics.fmean(values))}")
