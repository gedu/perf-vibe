"""Port contracts (`typing.Protocol`) the core depends on.

PURE MODULE — no adapter imports, no I/O. `domain/` and `application/`
depend ONLY on these Protocols; concrete implementations live in
`adapters/` (PR2) and are resolved by name through a registry (PR2), never
imported directly here or in `application/`.

Mirrors the master design §10 verbatim. All ports are defined here as the
stable shared seam, including ports `run`'s use-case does not call
(`Analyzer`, `Reporter`) — `compare`/`show`/`history` (later capabilities)
depend on the same contract without editing this file again.
"""

from __future__ import annotations

from typing import Iterable, Optional, Protocol, Sequence

from perf.domain.model import Marker, RunContext, SystemSample, Verdict


class FlowDriver(Protocol):
    """Launches the app and drives a named flow N times."""

    def run(self, flow_name: str, iterations: int, *, mode: str, restart: bool) -> None: ...


class MarkerSource(Protocol):
    """Yields in-app timing markers captured during the run (e.g. from logcat)."""

    def markers(self) -> Iterable[Marker]: ...


class SystemSampler(Protocol):
    """Yields per-iteration system metrics (e.g. from Flashlight results JSON)."""

    def samples(self) -> Iterable[SystemSample]: ...


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
        markers: Sequence[Marker],
        samples: Sequence[SystemSample],
    ) -> int: ...

    def history(
        self, flow_name: str, metric_name: str, device_key: str, limit: int
    ) -> Sequence["RunPoint"]: ...
    # ... show/history read models


class Analyzer(Protocol):
    """Computes percentiles + the regression verdict from stored history."""

    def compare_latest(self, flow_name: str, device_key: str) -> Sequence[Verdict]: ...


class Reporter(Protocol):
    """Renders results. PrettyReporter for humans, JsonReporter for machines."""

    def report(self, payload: "ReportPayload") -> None: ...


class Clock(Protocol):
    def now_utc_iso(self) -> str: ...
