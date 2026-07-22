"""`SystemSampler` port adapter — Flashlight (design §3, discovery #37).

Contributes an optional command-wrapper (pure `wrap()`) and parses the
resulting results JSON (I/O `parse()`). Per-iteration aggregation ONLY —
the ~94-sample time-series inside `measures[]` is never persisted, and the
raw report path is tracked by the caller (the `results_path` handed to
`wrap()`/`parse()` IS `run.raw_report_path` — this adapter does not need
to re-surface it separately).

HARD boundary (SKILL rule 9 / spec: "never ingest network metrics"): this
parser reads exactly the keys it names below (`time`, `startTime`, `fps`,
`ram`, `cpu.perName`) and nothing else — any other field present in the
Flashlight JSON (including a `network` block) is structurally never
touched, because `SystemSample` has no such field and nothing here
references that key.

Fix (CRITICAL resilience review): `status` (top-level AND per-iteration) is
now honored. A FAILURE/timed-out iteration is EXCLUDED from aggregation
(never blended into a normal `SystemSample`) and surfaced via
`SystemSampleParseResult.partial_coverage`. A non-SUCCESS top-level status
raises `FlashlightParseError` — a failed/incomplete run must never be
persisted as if it succeeded, which would poison the regression history.
"""

from __future__ import annotations

import json
import shlex
import statistics
from pathlib import Path
from typing import Optional, Union

from perf.domain.model import (
    DriverCommand,
    SamplerCommand,
    SystemSample,
    SystemSampleParseResult,
)


class FlashlightParseError(RuntimeError):
    """Raised when the Flashlight report's top-level `status` is not
    `SUCCESS` — refuses to aggregate/persist a failed or incomplete run as
    if it succeeded (CRITICAL resilience fix)."""


class FlashlightSampler:
    """`SystemSampler` (`domain/ports.py`) implementation."""

    def wrap(
        self,
        inner: DriverCommand,
        *,
        iterations: int,
        restart: bool,
        results_path: Union[str, Path],
    ) -> Optional[SamplerCommand]:
        if inner.argv is None:
            # Manual driver, no automated inner command — Flashlight's
            # `measure` seam for manual+Flashlight is documented but not
            # built in Phase 1 (design §3/§7).
            return None

        # `shlex.join` (stdlib) safely quotes each already-validated argv
        # element into the single string Flashlight's `--testCommand`
        # wants — never naive `" ".join`/string interpolation of raw
        # input (SKILL rule 5: this is the one spot where Flashlight
        # itself wants a string; it is built from already-validated argv
        # parts, never from unvalidated user text).
        inner_command = shlex.join(inner.argv)

        argv: list[str] = [
            "flashlight",
            "test",
            "--testCommand",
            inner_command,
            "--iterationCount",
            str(iterations),
            "--resultsFilePath",
            str(results_path),
        ]
        if not restart:
            # warm (default) -> --skipRestart; --restart forces cold ->
            # omit the flag (design §3 / §"CLI Options").
            argv.append("--skipRestart")

        return SamplerCommand(
            argv=argv,
            results_path=str(results_path),
            manages_iterations=True,
        )

    def parse(self, results_path: Union[str, Path]) -> SystemSampleParseResult:
        raw = json.loads(Path(results_path).read_text())

        top_status = raw.get("status")
        if top_status is not None and top_status != "SUCCESS":
            # Never aggregate/persist a failed or incomplete run as if it
            # succeeded — that would poison the regression history.
            raise FlashlightParseError(
                f"Flashlight report status is {top_status!r}, not 'SUCCESS' — "
                "refusing to aggregate a failed/incomplete run."
            )

        samples: list[SystemSample] = []
        partial_coverage = False

        for idx, iteration in enumerate(raw.get("iterations", [])):
            iter_status = iteration.get("status")
            if iter_status is not None and iter_status != "SUCCESS":
                # Exclude the failed iteration from aggregation entirely —
                # it never becomes a normal-looking SystemSample — and
                # surface the gap as partial coverage.
                partial_coverage = True
                continue

            measures = iteration.get("measures", [])

            fps_values = [m["fps"] for m in measures if "fps" in m]
            ram_values = [m["ram"] for m in measures if "ram" in m]
            cpu_totals = [
                sum(m["cpu"]["perName"].values())
                for m in measures
                if "cpu" in m and "perName" in m["cpu"]
            ]

            samples.append(
                SystemSample(
                    iteration_idx=idx,
                    total_time_ms=iteration.get("time"),
                    start_time_ms=iteration.get("startTime"),
                    fps_avg=statistics.fmean(fps_values) if fps_values else None,
                    fps_min=min(fps_values) if fps_values else None,
                    ram_avg_mb=statistics.fmean(ram_values) if ram_values else None,
                    ram_peak_mb=max(ram_values) if ram_values else None,
                    cpu_avg_pct=statistics.fmean(cpu_totals) if cpu_totals else None,
                    cpu_peak_pct=max(cpu_totals) if cpu_totals else None,
                )
            )

        return SystemSampleParseResult(samples=samples, partial_coverage=partial_coverage)
