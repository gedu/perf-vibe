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
import math
import shlex
import statistics
from pathlib import Path

from perf.domain.model import (
    DriverCommand,
    SamplerCommand,
    SystemSample,
    SystemSampleParseResult,
)


def _is_finite_number(value: object) -> bool:
    """`json.loads` ACCEPTS `NaN`/`Infinity` literals, and a report can carry
    non-numeric junk. A NaN reaching `fmean` poisons the whole aggregate
    (and later binds as NULL into the nullable `system_sample` columns,
    silently vanishing). Bad values are skipped exactly like a missing key.
    `bool` is excluded — `True` is an `int` subclass, not a measurement."""

    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _finite_or_none(value: object) -> float | None:
    return float(value) if _is_finite_number(value) else None  # type: ignore[arg-type]


class FlashlightParseError(RuntimeError):
    """Raised when the Flashlight report's top-level `status` is not
    `SUCCESS` — refuses to aggregate/persist a failed or incomplete run as
    if it succeeded (CRITICAL resilience fix)."""


class FlashlightSampler:
    """`SystemSampler` (`domain/ports.py`) implementation.

    `bundle_id` is Flashlight's REQUIRED `--bundleId` — the app under
    measurement. It is threaded from `perfvibe.toml`'s `bundle_id` key (written
    by `perf init`, resolved by `config/loader.py`) at composition time.
    Kept `Optional` at construction so parse-only uses need not supply it,
    but `wrap()` refuses to build an invalid Flashlight command without it
    (see below)."""

    def __init__(self, *, bundle_id: str | None = None) -> None:
        self._bundle_id = bundle_id

    def wrap(
        self,
        inner: DriverCommand,
        *,
        iterations: int,
        restart: bool,
        results_path: str | Path,
    ) -> SamplerCommand | None:
        if inner.argv is None:
            # Manual driver, no automated inner command — Flashlight's
            # `measure` seam for manual+Flashlight is documented but not
            # built in Phase 1 (design §3/§7). No `--bundleId` needed here:
            # there is no command to build.
            return None

        if not self._bundle_id:
            # Flashlight's `test` requires `--bundleId`; without it the real
            # binary aborts with a cryptic commander error. Fail LOUD and
            # EARLY at the adapter boundary (before any device/tool touch),
            # regardless of which caller composed this wrap — this is the
            # sampler's own invariant, not the CLI's. The use-case remaps
            # this `ValueError` to a usage error (exit 2).
            raise ValueError(
                "flashlight sampler requires a bundle_id (Flashlight's "
                "--bundleId, the app under measurement); run `perfvibe init` "
                "to detect it or set bundle_id in perfvibe.toml"
            )

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
            "--bundleId",
            self._bundle_id,
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

    def parse(self, results_path: str | Path) -> SystemSampleParseResult:
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
        # `run-live-progress` Slice C: the FULL per-iteration ok/not-ok list,
        # index-aligned with `iterations[]` — unlike `samples`, a failed
        # iteration still gets an entry here (as `False`) rather than being
        # dropped, so the CLI recap can show a TRUE per-iteration status
        # instead of fabricating one.
        iteration_statuses: list[bool] = []

        for idx, iteration in enumerate(raw.get("iterations", [])):
            iter_status = iteration.get("status")
            ok = iter_status is None or iter_status == "SUCCESS"
            iteration_statuses.append(ok)
            if not ok:
                # Exclude the failed iteration from aggregation entirely —
                # it never becomes a normal-looking SystemSample — and
                # surface the gap as partial coverage.
                partial_coverage = True
                continue

            measures = iteration.get("measures", [])

            fps_values = [m["fps"] for m in measures if _is_finite_number(m.get("fps"))]
            ram_values = [m["ram"] for m in measures if _is_finite_number(m.get("ram"))]
            cpu_totals: list[float] = []
            for m in measures:
                if "cpu" not in m or "perName" not in m["cpu"]:
                    continue
                finite = [v for v in m["cpu"]["perName"].values() if _is_finite_number(v)]
                # A perName map with ONLY bad values counts as a missing
                # measure, never as a bogus 0% total.
                if finite:
                    cpu_totals.append(sum(finite))

            samples.append(
                SystemSample(
                    iteration_idx=idx,
                    total_time_ms=_finite_or_none(iteration.get("time")),
                    start_time_ms=_finite_or_none(iteration.get("startTime")),
                    fps_avg=statistics.fmean(fps_values) if fps_values else None,
                    fps_min=min(fps_values) if fps_values else None,
                    ram_avg_mb=statistics.fmean(ram_values) if ram_values else None,
                    ram_peak_mb=max(ram_values) if ram_values else None,
                    cpu_avg_pct=statistics.fmean(cpu_totals) if cpu_totals else None,
                    cpu_peak_pct=max(cpu_totals) if cpu_totals else None,
                )
            )

        return SystemSampleParseResult(
            samples=samples,
            partial_coverage=partial_coverage,
            iteration_statuses=iteration_statuses,
        )
