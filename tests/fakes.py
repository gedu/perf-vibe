"""Shared test doubles for every port (SKILL rule 8: "Every side effect is
behind a port and faked"). Used to drive `RunFlowUseCase` end-to-end with
NO real device, subprocess, or filesystem I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from perf.domain.model import (
    CaptureSpec,
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

__all__ = [
    "FakeDriver",
    "FakeMarkerSource",
    "FakeRunContextProvider",
    "FakeStore",
    "FakeSystemSampler",
    "FrozenClock",
    "NoArgRunContextProvider",
    "SequentialClock",
    "make_run_context",
]


class FrozenClock:
    """`Clock` (`domain/ports.py`) fake — deterministic `now_utc_iso()`."""

    def __init__(self, fixed: str = "2026-01-01T00:00:00+00:00") -> None:
        self.fixed = fixed

    def now_utc_iso(self) -> str:
        return self.fixed


class SequentialClock:
    """`Clock` fake that advances by one second on every call — used to
    seed a deterministically ORDERED multi-run/multi-commit history (e.g.
    `compare`'s baseline read-model tests need `started_at` to sort
    chronologically across many seeded `save_run` calls)."""

    def __init__(self, start: str = "2020-01-01T00:00:00+00:00") -> None:
        self._t = datetime.fromisoformat(start)
        if self._t.tzinfo is None:
            self._t = self._t.replace(tzinfo=UTC)

    def now_utc_iso(self) -> str:
        self._t += timedelta(seconds=1)
        return self._t.isoformat()


class FakeDriver:
    """`FlowDriver` fake. `command_error` simulates a bad/unknown flow name
    (mirrors `MaestroDriver.command()` raising `ValueError`).
    `drive_error` simulates a device/tool failure raised as an `OSError`.
    """

    def __init__(
        self,
        *,
        drive_result: DriverResult | None = None,
        command_error: Exception | None = None,
        drive_error: Exception | None = None,
        automated: bool = True,
        prompt: str = "Perform the flow manually, then confirm.",
    ) -> None:
        self._drive_result = drive_result or DriverResult(
            ok=True, iteration_outcomes=("ok",), logcat_lines=()
        )
        self._command_error = command_error
        self._drive_error = drive_error
        self._automated = automated
        self._prompt = prompt
        self.commands_requested: list = []
        self.drive_calls: list[ExecutionPlan] = []

    def command(
        self,
        flow_name: str,
        *,
        mode: str,
        restart: bool,
        env: Mapping[str, str] | None = None,
    ) -> DriverCommand:
        self.commands_requested.append((flow_name, mode, restart, env))
        if self._command_error is not None:
            raise self._command_error
        if not self._automated:
            # Mirrors `ManualDriver.command()`: no automated argv, only a
            # prompt — selects `LoopMode.DRIVER_MANAGED` in
            # `compose_execution_plan`.
            return DriverCommand(argv=None, automated=False, prompt=self._prompt)
        return DriverCommand(argv=["fake-driver", flow_name], automated=True)

    def drive(self, plan: ExecutionPlan) -> DriverResult:
        self.drive_calls.append(plan)
        if self._drive_error is not None:
            raise self._drive_error
        return self._drive_result


class FakeSystemSampler:
    """`SystemSampler` fake. `wrap_result=None` (the default) simulates a
    driver/sampler combo that CAN wrap (returns a `SamplerCommand`); pass
    `wrap_returns_none=True` to simulate the manual+Flashlight documented
    seam where `wrap()` itself returns `None`."""

    def __init__(
        self,
        *,
        wrap_returns_none: bool = False,
        parse_result: SystemSampleParseResult | None = None,
        parse_error: Exception | None = None,
    ) -> None:
        self._wrap_returns_none = wrap_returns_none
        self._parse_result = parse_result or SystemSampleParseResult(
            samples=(), partial_coverage=False
        )
        self._parse_error = parse_error
        self.wrap_calls: list = []
        self.parse_calls: list = []

    def wrap(
        self,
        inner: DriverCommand,
        *,
        iterations: int,
        restart: bool,
        results_path: str,
    ) -> SamplerCommand | None:
        self.wrap_calls.append((inner, iterations, restart, results_path))
        if self._wrap_returns_none:
            return None
        return SamplerCommand(
            argv=["fake-sampler", results_path],
            results_path=results_path,
            manages_iterations=True,
        )

    def parse(self, results_path: str) -> SystemSampleParseResult:
        self.parse_calls.append(results_path)
        if self._parse_error is not None:
            raise self._parse_error
        return self._parse_result


class FakeMarkerSource:
    """`MarkerSource` fake."""

    def __init__(
        self,
        *,
        capture: CaptureSpec | None = None,
        parse_result: MarkerParseResult | None = None,
    ) -> None:
        self._capture = capture or CaptureSpec(argv=["fake-logcat"])
        self._parse_result = parse_result or MarkerParseResult(markers=(), partial_coverage=False)
        self.parse_calls: list = []

    def capture_spec(self) -> CaptureSpec | None:
        return self._capture

    def parse(self, lines: Sequence[str], *, iterations: int) -> MarkerParseResult:
        self.parse_calls.append((tuple(lines), iterations))
        return self._parse_result


def make_run_context(**overrides) -> RunContext:
    defaults = dict(
        device_key="Pixel-Fake|14|physical",
        model="Pixel-Fake",
        os_version="14",
        is_emulator=False,
        source="local:test",
        git_commit="abc123",
        git_branch="main",
        app_version=None,
        is_dev_bundle=None,
        bundle_source=None,
        build_variant=None,
        tool_version="0.0.0-test",
    )
    defaults.update(overrides)
    return RunContext(**defaults)


class FakeRunContextProvider:
    """`RunContextProvider` fake mirroring `BashRunContextProvider`'s
    documented OPTIONAL `logcat_lines` extension (still Protocol-compatible
    — a bare `context()` call also works)."""

    def __init__(self, ctx: RunContext | None = None) -> None:
        self._ctx = ctx or make_run_context()
        self.calls: list = []

    def context(self, logcat_lines: Sequence[str] = ()) -> RunContext:
        self.calls.append(tuple(logcat_lines))
        return self._ctx


class NoArgRunContextProvider:
    """A STRICT `RunContextProvider` — only the bare Protocol signature,
    `context(self) -> RunContext`, with no optional extension. Proves
    `RunFlowUseCase._get_context` falls back correctly instead of crashing
    on a `TypeError`."""

    def __init__(self, ctx: RunContext | None = None) -> None:
        self._ctx = ctx or make_run_context()
        self.calls = 0

    def context(self) -> RunContext:
        self.calls += 1
        return self._ctx


class FakeStore:
    """`Store` fake. `save_error` simulates a store-level failure (e.g. a
    disk error mid-transaction) to prove the use-case does not swallow it."""

    def __init__(self, *, save_error: Exception | None = None) -> None:
        self._save_error = save_error
        self._next_id = 1
        self.saved_runs: list[dict] = []

    def save_run(
        self,
        ctx: RunContext,
        flow_name: str,
        iterations: int,
        mode: str,
        source: str,
        markers: Sequence[Marker],
        samples: Sequence[SystemSample],
        raw_report_path: str | None,
    ) -> int:
        if self._save_error is not None:
            raise self._save_error
        run_id = self._next_id
        self._next_id += 1
        self.saved_runs.append(
            {
                "run_id": run_id,
                "ctx": ctx,
                "flow_name": flow_name,
                "iterations": iterations,
                "mode": mode,
                "source": source,
                "markers": tuple(markers),
                "samples": tuple(samples),
                "raw_report_path": raw_report_path,
            }
        )
        return run_id

    def history(
        self, flow_name: str, metric_name: str, device_key: str, limit: int
    ) -> Sequence:
        return ()
