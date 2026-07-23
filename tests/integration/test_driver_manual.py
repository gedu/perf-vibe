"""Integration tests for `ManualDriver` (design §1: "Manual path:
inner.argv=None, command=None, loop=DRIVER_MANAGED; drive() prompts N times
while logcat captures.").

RED-before-GREEN: written before `src/perf/adapters/driver_manual.py`
existed. Driven entirely via a fake input function and a fake process
runner — no device required.
"""

from __future__ import annotations

from perf.adapters.driver_manual import ManualDriver
from perf.adapters.process import CaptureResult
from perf.domain.model import CaptureSpec, ExecutionPlan, LoopMode


class _FakeInput:
    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return ""


class _FakeRunner:
    def __init__(self, capture_returncode: int = 0):
        self.capture_started: list[list[str]] = []
        self.capture_stopped = 0
        self._capture_returncode = capture_returncode

    def start_capture(self, argv):
        self.capture_started.append(list(argv))
        return object()

    def stop_capture(self, handle):
        self.capture_stopped += 1
        return CaptureResult(
            lines=["[PERF] onboarding: 300ms"], returncode=self._capture_returncode
        )


def test_command_returns_no_argv_and_carries_a_prompt():
    driver = ManualDriver({"onboarding": "Perform onboarding end-to-end."})
    cmd = driver.command("onboarding", mode="warm", restart=False)
    assert cmd.argv is None
    assert cmd.automated is False
    assert cmd.prompt == "Perform onboarding end-to-end."


def test_command_falls_back_to_a_generic_prompt_for_unconfigured_flow():
    driver = ManualDriver({})
    cmd = driver.command("some-flow", mode="warm", restart=False)
    assert cmd.argv is None
    assert "some-flow" in cmd.prompt


def test_drive_prompts_once_per_iteration_via_fake_stdin_no_device():
    fake_input = _FakeInput()
    runner = _FakeRunner()
    driver = ManualDriver({}, runner=runner, input_fn=fake_input)
    inner_cmd = driver.command("onboarding", mode="warm", restart=False)
    plan = ExecutionPlan(
        command=None,
        inner=inner_cmd,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=3,
        capture=CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"]),
        results_path=None,
    )

    result = driver.drive(plan)

    assert len(fake_input.prompts) == 3
    assert result.ok is True
    assert len(result.iteration_outcomes) == 3
    assert result.logcat_lines == ("[PERF] onboarding: 300ms",)
    assert runner.capture_started == [["adb", "logcat", "-s", "ReactNativeJS:V"]]
    assert runner.capture_stopped == 1


def test_drive_without_marker_capture_returns_empty_logcat_lines():
    driver = ManualDriver({}, input_fn=_FakeInput())
    inner_cmd = driver.command("onboarding", mode="warm", restart=False)
    plan = ExecutionPlan(
        command=None,
        inner=inner_cmd,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=1,
        capture=None,
        results_path=None,
    )
    result = driver.drive(plan)
    assert result.logcat_lines == ()
    assert result.capture_failed is False


def test_dead_logcat_capture_surfaces_as_capture_failed():
    """Fix (resilience review): ManualDriver owns the same parallel logcat
    capture lifecycle as MaestroDriver, so it must surface a dead capture
    the same way — never silently indistinguishable from zero markers."""
    fake_input = _FakeInput()
    runner = _FakeRunner(capture_returncode=1)
    driver = ManualDriver({}, runner=runner, input_fn=fake_input)
    inner_cmd = driver.command("onboarding", mode="warm", restart=False)
    plan = ExecutionPlan(
        command=None,
        inner=inner_cmd,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=1,
        capture=CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"]),
        results_path=None,
    )
    result = driver.drive(plan)
    assert result.capture_failed is True
