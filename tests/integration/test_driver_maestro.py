"""Integration tests for `MaestroDriver` (design §1/§3).

RED-before-GREEN: written before `src/perf/adapters/driver_maestro.py`
existed. ALWAYS asserts the composed command is an argv LIST (never a
shell string) and that `flow_name` is validated against the config-known
flow set BEFORE any subprocess is spawned. A fake `SubprocessRunner`
stands in for adb/maestro — no real device or Flashlight binary involved.
"""

from __future__ import annotations

import inspect

import pytest

from perf.adapters.driver_maestro import MaestroDriver
from perf.adapters.process import CaptureResult, CommandResult
from perf.domain.model import CaptureSpec, DriverCommand, ExecutionPlan, LoopMode

_KNOWN_FLOWS = {"checkout": "flows/checkout.yaml"}


class _FakeRunner:
    def __init__(self, returncode: int = 0, stderr: str = "", capture_returncode: int = 0):
        self.run_calls: list[list[str]] = []
        self.capture_started: list[list[str]] = []
        self.capture_stopped = 0
        self._returncode = returncode
        self._stderr = stderr
        self._capture_returncode = capture_returncode

    def run(self, argv, **kwargs):
        self.run_calls.append(list(argv))
        return CommandResult(returncode=self._returncode, stdout="", stderr=self._stderr)

    def start_capture(self, argv):
        self.capture_started.append(list(argv))
        return object()  # opaque handle

    def stop_capture(self, handle):
        self.capture_stopped += 1
        return CaptureResult(lines=["[PERF] checkout: 900ms"], returncode=self._capture_returncode)


def test_command_is_an_argv_list_not_a_string():
    driver = MaestroDriver(_KNOWN_FLOWS, runner=_FakeRunner())
    cmd = driver.command("checkout", mode="warm", restart=False)
    assert isinstance(cmd.argv, list)
    assert cmd.argv == ["maestro", "test", "flows/checkout.yaml"]


def test_command_includes_device_pinning_when_configured():
    driver = MaestroDriver(_KNOWN_FLOWS, device="emulator-5554", runner=_FakeRunner())
    cmd = driver.command("checkout", mode="warm", restart=False)
    assert cmd.argv == ["maestro", "--device", "emulator-5554", "test", "flows/checkout.yaml"]


def test_command_forwards_env_secrets_as_maestro_env_flags_never_a_shell_string():
    driver = MaestroDriver(_KNOWN_FLOWS, runner=_FakeRunner())
    cmd = driver.command("checkout", mode="warm", restart=False, env={"PASSWORD": "s3cr3t"})
    assert cmd.argv == ["maestro", "test", "flows/checkout.yaml", "--env", "PASSWORD=s3cr3t"]
    assert isinstance(cmd.argv, list)


def test_unknown_flow_is_rejected_before_any_subprocess_spawn():
    runner = _FakeRunner()
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    with pytest.raises(ValueError):
        driver.command("not-a-configured-flow", mode="warm", restart=False)
    assert runner.run_calls == []
    assert runner.capture_started == []


def test_drive_driver_managed_runs_inner_command_n_times_and_captures_logcat():
    runner = _FakeRunner()
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    plan = ExecutionPlan(
        command=None,
        inner=inner,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=3,
        capture=CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"]),
        results_path=None,
    )

    result = driver.drive(plan)

    assert len(runner.run_calls) == 3
    assert all(call == list(inner.argv) for call in runner.run_calls)
    assert runner.capture_started == [["adb", "logcat", "-s", "ReactNativeJS:V"]]
    assert runner.capture_stopped == 1
    assert result.ok is True
    assert result.logcat_lines == ("[PERF] checkout: 900ms",)


def test_drive_tool_managed_spawns_the_composed_command_once():
    runner = _FakeRunner()
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    wrapped_command = [
        "flashlight",
        "test",
        "--testCommand",
        "maestro test flows/checkout.yaml",
        "--iterationCount",
        "3",
        "--resultsFilePath",
        "r.json",
        "--skipRestart",
    ]
    plan = ExecutionPlan(
        command=wrapped_command,
        inner=inner,
        loop_mode=LoopMode.TOOL_MANAGED,
        iterations=3,
        capture=None,
        results_path="r.json",
    )

    result = driver.drive(plan)

    assert runner.run_calls == [wrapped_command]
    assert result.ok is True
    assert runner.capture_started == []  # no MarkerSource active -> no logcat capture


def test_drive_failure_is_reported_not_swallowed():
    runner = _FakeRunner(returncode=1)
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    plan = ExecutionPlan(
        command=None,
        inner=inner,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=2,
        capture=None,
        results_path=None,
    )
    result = driver.drive(plan)
    assert result.ok is False


def test_dead_logcat_capture_surfaces_as_capture_failed_not_empty_markers():
    """Fix (resilience review): a non-zero logcat exit (e.g. adb's 'more
    than one device' error on a multi-device host) must be signalled
    distinctly from a healthy capture that simply saw zero marker lines —
    otherwise both cases look identical ('no markers'), and the run
    silently loses data indistinguishable from 'flow emitted none'."""
    runner = _FakeRunner(capture_returncode=1)
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    plan = ExecutionPlan(
        command=None,
        inner=inner,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=1,
        capture=CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"]),
        results_path=None,
    )

    result = driver.drive(plan)

    assert result.capture_failed is True


def test_healthy_capture_with_zero_markers_does_not_flag_capture_failed():
    runner = _FakeRunner(capture_returncode=0)
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    plan = ExecutionPlan(
        command=None,
        inner=inner,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=1,
        capture=CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"]),
        results_path=None,
    )

    result = driver.drive(plan)

    assert result.capture_failed is False


def test_failed_drive_populates_diagnostics_with_tool_stderr():
    """Fix (WARNING review): a failed run must tell the user WHICH
    tool/flow/device failed and WHY — stderr must not be discarded."""
    runner = _FakeRunner(returncode=1, stderr="Error: could not find device")
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    plan = ExecutionPlan(
        command=None,
        inner=inner,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=1,
        capture=None,
        results_path=None,
    )

    result = driver.drive(plan)

    assert result.ok is False
    assert result.diagnostics is not None
    assert "could not find device" in result.diagnostics


def test_successful_drive_leaves_diagnostics_none():
    runner = _FakeRunner(returncode=0)
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    plan = ExecutionPlan(
        command=None,
        inner=inner,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=1,
        capture=None,
        results_path=None,
    )

    result = driver.drive(plan)

    assert result.ok is True
    assert result.diagnostics is None


def test_password_secret_never_appears_in_diagnostics():
    """Do NOT let secrets (PASSWORD) leak into diagnostics — the value
    forwarded via `--env PASSWORD=...` must be scrubbed even if it happens
    to appear in the tool's own stderr output."""
    runner = _FakeRunner(returncode=1, stderr="auth failed for user with PASSWORD=s3cr3t")
    driver = MaestroDriver(_KNOWN_FLOWS, runner=runner)
    cmd = driver.command("checkout", mode="warm", restart=False, env={"PASSWORD": "s3cr3t"})
    plan = ExecutionPlan(
        command=None,
        inner=cmd,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=1,
        capture=None,
        results_path=None,
    )

    result = driver.drive(plan)

    assert result.diagnostics is not None
    assert "s3cr3t" not in result.diagnostics
    assert "***" in result.diagnostics


def test_real_subprocess_runner_never_uses_shell_true():
    """Source-level guard on the REAL `SubprocessRunner` (used when no fake
    is injected): never passes `shell=True` to `subprocess` (SKILL rule 5)."""
    from perf.adapters import process as process_module

    source = inspect.getsource(process_module)
    assert "shell=True" not in source
    assert "shell = True" not in source
