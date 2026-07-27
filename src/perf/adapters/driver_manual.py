"""`FlowDriver` port adapter — no automation (design §1: "Manual path:
inner.argv=None, command=None, loop=DRIVER_MANAGED; drive() prompts N
times while logcat captures.").

`command()` is PURE: it never touches a device, only returns an
instruction `prompt` (per-flow if configured, else a generic fallback) and
`argv=None` so `compose_execution_plan` selects `DRIVER_MANAGED`.

`drive()` emits the instruction via the injected `ProgressReporter`
(STDERR-bound in the real CLI reporter — `run-live-progress` design "Where
the port is injected") and waits for the user to confirm each iteration
(`input()` by default, injectable for tests), while owning the same
parallel `adb logcat` capture lifecycle as `MaestroDriver` (start before,
stop after) — it just never spawns maestro/flashlight itself.

Fix (`run-live-progress`, Slice A): the prompt/confirmation used to go via
raw `print`/`input` straight to STDOUT, corrupting the `--json` contract.
It now goes ONLY through `reporter.awaiting_user_input` — `input_fn` is
called with an EMPTY prompt so a real `input()` never echoes text to
STDOUT a second time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from perf.adapters.process import SubprocessRunner, bounded_diagnostics
from perf.domain.model import DriverCommand, DriverResult, ExecutionPlan
from perf.domain.ports import ProgressReporter


class _NoOpProgressReporter:
    """Internal fallback when no `reporter` is injected — mirrors the
    `runner`-defaults-to-`SubprocessRunner()` idiom already used here.
    Every event is a no-op. Kept private and minimal rather than importing
    the concrete `NullProgressReporter` (`cli/output/progress.py`, Slice
    B), which does not exist yet and would be a `cli/` dependency inside
    `adapters/` anyway."""

    def iteration_started(self, index: int, total: int) -> None:
        pass

    def iteration_finished(self, index: int, total: int, *, ok: bool) -> None:
        pass

    def awaiting_user_input(self, prompt: str) -> None:
        pass

    def relayed_line(self, text: str) -> None:
        pass


class ManualDriver:
    """`FlowDriver` (`domain/ports.py`) implementation."""

    def __init__(
        self,
        flow_prompts: Mapping[str, str],
        *,
        runner: SubprocessRunner | None = None,
        reporter: ProgressReporter | None = None,
        input_fn: Callable[[str], str] | None = None,
    ) -> None:
        self._flow_prompts = dict(flow_prompts)
        self._runner = runner if runner is not None else SubprocessRunner()
        self._reporter: ProgressReporter = (
            reporter if reporter is not None else _NoOpProgressReporter()
        )
        self._input_fn = input_fn if input_fn is not None else input

    def command(
        self,
        flow_name: str,
        *,
        mode: str,
        restart: bool,
        env: Mapping[str, str] | None = None,
    ) -> DriverCommand:
        del mode, restart, env  # no automated command depends on these
        prompt = self._flow_prompts.get(
            flow_name, f"Perform the '{flow_name}' flow manually, then confirm."
        )
        return DriverCommand(argv=None, automated=False, prompt=prompt)

    def drive(self, plan: ExecutionPlan) -> DriverResult:
        logcat_process = None
        if plan.capture is not None:
            logcat_process = self._runner.start_capture(list(plan.capture.argv))

        logcat_lines: Sequence[str] = ()
        capture_failed = False
        diagnostics: str | None = None
        try:
            prompt = plan.inner.prompt or "Perform the flow manually, then press Enter."
            iteration_outcomes: list[str] = []
            for i in range(plan.iterations):
                self._reporter.iteration_started(i + 1, plan.iterations)
                self._reporter.awaiting_user_input(f"[{i + 1}/{plan.iterations}] {prompt}")
                # The prompt already reached the reporter — pass an EMPTY
                # prompt to `input_fn` so a real `input()` never echoes it
                # to STDOUT a second time (the fixed --json-corruption bug).
                self._input_fn("")
                iteration_outcomes.append("ok")
                self._reporter.iteration_finished(i + 1, plan.iterations, ok=True)
        finally:
            if logcat_process is not None:
                # Same fix as MaestroDriver: a dead/failed logcat capture
                # must be signalled distinctly from zero markers.
                capture_result = self._runner.stop_capture(logcat_process)
                logcat_lines = tuple(capture_result.lines)
                if capture_result.returncode not in (None, 0):
                    capture_failed = True
                    diagnostics = bounded_diagnostics("\n".join(capture_result.lines))

        return DriverResult(
            ok=True,
            iteration_outcomes=tuple(iteration_outcomes),
            logcat_lines=logcat_lines,
            capture_failed=capture_failed,
            diagnostics=diagnostics,
        )
