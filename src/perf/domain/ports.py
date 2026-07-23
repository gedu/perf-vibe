"""Port contracts (`typing.Protocol`) the core depends on — REVISION 2.

PURE MODULE — no adapter imports, no I/O. `domain/` and `application/`
depend ONLY on these Protocols; concrete implementations live in
`adapters/` (PR2) and are resolved by name through a registry (PR2), never
imported directly here or in `application/`.

Rev 2 (design `perf-cli/design/perf-run` #31, §"Key ports") rewrites the
six core Protocols so FlowDriver/SystemSampler/MarkerSource each expose a
pure compose-time method plus an I/O (or pure-parse) method, resolving the
Flashlight-wraps-Maestro coupling via a compose-time `ExecutionPlan`
(design §1) rather than a composite adapter. `Analyzer`/`Reporter` are kept
verbatim as the stable shared seam `compare`/`show`/`history` (later
capabilities) will depend on without editing this file again — `run`'s
use-case never calls them.
"""

from __future__ import annotations

from typing import Mapping, Optional, Protocol, Sequence

from perf.domain.model import (
    CaptureSpec,
    CompareResult,
    DriverCommand,
    DriverResult,
    ExecutionPlan,
    Marker,
    MarkerParseResult,
    RunContext,
    SamplerCommand,
    SystemSample,
    SystemSampleParseResult,
)


class FlowDriver(Protocol):
    """Contributes the inner test command (pure `command()`) and owns the
    OS process + parallel-logcat lifecycle for an assembled
    `ExecutionPlan` (I/O `drive()`). Agnostic to WHAT `plan.command` is —
    it may be a raw Maestro invocation or a Flashlight-wrapped one."""

    def command(
        self,
        flow_name: str,
        *,
        mode: str,
        restart: bool,
        env: Optional[Mapping[str, str]] = None,
    ) -> DriverCommand: ...

    def drive(self, plan: ExecutionPlan) -> DriverResult: ...


class SystemSampler(Protocol):
    """Contributes an optional command-wrapper (pure `wrap()` — `None`
    when this sampler cannot wrap, e.g. a documented-but-unbuilt seam) and
    later parses the artifact it declared (I/O `parse()`).

    `parse()` returns a `SystemSampleParseResult`, not a bare list, so a
    parser can flag partial coverage when one or more iterations reported a
    non-SUCCESS status (fix: never silently aggregate a failed iteration as
    if it succeeded)."""

    def wrap(
        self,
        inner: DriverCommand,
        *,
        iterations: int,
        restart: bool,
        results_path: str,
    ) -> Optional[SamplerCommand]: ...

    def parse(self, results_path: str) -> SystemSampleParseResult: ...


class MarkerSource(Protocol):
    """Contributes the logcat capture spec (pure `capture_spec()` — `None`
    when this source needs no parallel capture) and parses the buffer the
    driver returns (pure `parse()` — no I/O of its own; the driver already
    captured the lines)."""

    def capture_spec(self) -> Optional[CaptureSpec]: ...

    def parse(self, lines: Sequence[str], *, iterations: int) -> MarkerParseResult: ...


class RunContextProvider(Protocol):
    """Assembles run metadata from env facts + app-emitted [PERF-META]."""

    def context(self) -> RunContext: ...


class Store(Protocol):
    """Persists a run and answers history queries."""

    def save_run(
        self,
        ctx: RunContext,
        flow_name: str,
        iterations: int,
        mode: str,
        source: str,
        markers: Sequence[Marker],
        samples: Sequence[SystemSample],
        raw_report_path: Optional[str],
    ) -> int: ...

    def history(
        self, flow_name: str, metric_name: str, device_key: str, limit: int
    ) -> Sequence["RunPoint"]: ...
    # ... show/history read models


class Analyzer(Protocol):
    """Computes percentiles + the regression verdict from stored history.

    Rev 3 (tasks #59): returns a single additive `CompareResult` carrier
    (verdicts + a `CalibrationReport`) instead of a bare `Sequence[Verdict]`
    — one method, kept additive since no other `Analyzer` implementer
    exists yet. `None` when the flow/device/mode combination has no runs
    at all (corner case C2/C7 — the CLI, PR-C, maps this to a usage
    error)."""

    def compare_latest(
        self, flow_name: str, device_key: str, mode: str = "warm"
    ) -> Optional[CompareResult]: ...


class Reporter(Protocol):
    """Renders results. PrettyReporter for humans, JsonReporter for machines."""

    def report(self, payload: "ReportPayload") -> None: ...


class Clock(Protocol):
    def now_utc_iso(self) -> str: ...
