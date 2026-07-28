"""`FlowDriver` port adapter — Maestro (design §1/§3).

`command()` is PURE: it validates `flow_name` against the CONFIG-KNOWN
flow set (`known_flows`, injected at construction — never a hardcoded or
freeform path) BEFORE any subprocess is ever spawned, and builds the inner
`maestro test <flow.yaml>` invocation as an argv LIST (device pinning via
`--device`, secret forwarding via `--env KEY=VALUE`) — NEVER a shell
string (SKILL rule 5).

`drive()` executes whatever `plan.command` the use-case already composed
(design §1: the Flashlight-wraps-Maestro coupling is resolved as DATA by
`compose_execution_plan`, not by one adapter knowing another) — this
driver is agnostic to whether that command is a raw Maestro invocation or
a Flashlight-wrapped one, and NEVER itself builds a `flashlight` command.
It ALSO owns the parallel `adb logcat` capture lifecycle (start before,
stop after) because only the driver knows flow timing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from perf.adapters.process import SubprocessRunner, bounded_diagnostics, scrub_secrets
from perf.domain.model import DriverCommand, DriverResult, ExecutionPlan, LoopMode
from perf.domain.ports import ProgressReporter


class _NoOpProgressReporter:
    """Internal fallback when no `reporter` is injected — mirrors
    `ManualDriver`'s private `_NoOpProgressReporter` (`driver_manual.py`).
    Every event is a no-op. Kept private and minimal rather than importing
    the concrete `NullProgressReporter` (`cli/output/progress.py`), which
    would be a `cli/` dependency inside `adapters/` for the common case
    (no reporter injected at all)."""

    def iteration_started(self, index: int, total: int) -> None:
        pass

    def iteration_finished(self, index: int, total: int, *, ok: bool) -> None:
        pass

    def awaiting_user_input(self, prompt: str) -> None:
        pass

    def relayed_line(self, text: str) -> None:
        pass


class MaestroDriver:
    """`FlowDriver` (`domain/ports.py`) implementation."""

    def __init__(
        self,
        known_flows: Mapping[str, str],
        *,
        device: str | None = None,
        runner: SubprocessRunner | None = None,
        reporter: ProgressReporter | None = None,
    ) -> None:
        # `reporter` joins the uniform driver-builder kwargs (mirrors
        # `runner`). Slice B's DRIVER_MANAGED loop emits iteration events +
        # relayed lines through it; Slice C's TOOL_MANAGED relay does the
        # same (`run-live-progress` design, tasks B.6/C.2).
        self._known_flows = dict(known_flows)
        self._device = device
        self._runner = runner if runner is not None else SubprocessRunner()
        self._reporter: ProgressReporter = (
            reporter if reporter is not None else _NoOpProgressReporter()
        )

    def command(
        self,
        flow_name: str,
        *,
        mode: str,
        restart: bool,
        env: Mapping[str, str] | None = None,
    ) -> DriverCommand:
        # Rejected BEFORE any subprocess spawn — flow_name is validated
        # against the config-known flow set, never trusted as-is
        # (SKILL rule 5).
        if flow_name not in self._known_flows:
            raise ValueError(
                f"Unknown flow {flow_name!r}; must be one of the config-known "
                f"flows {sorted(self._known_flows)!r}"
            )
        flow_path = self._known_flows[flow_name]

        # `mode`/`restart` do not affect the inner maestro invocation
        # itself (they shape the Flashlight wrap's --skipRestart flag,
        # design §3) — accepted here only to satisfy the `FlowDriver`
        # Protocol signature.
        del mode, restart

        argv: list[str] = ["maestro"]
        if self._device is not None:
            argv += ["--device", self._device]
        argv += ["test", flow_path]
        # Slice C (`run-live-progress`, real-binary-confirmed): piped Maestro
        # output is already clean plain text with no ANSI/cursor bytes, but
        # `--no-ansi` is passed explicitly anyway for a stable, tool-
        # guaranteed contract rather than relying on Maestro's own pipe
        # auto-detection. Placed BEFORE any `--env` secret flags. Because
        # Flashlight wraps this argv verbatim via `--testCommand` (design
        # §3), this also reaches the TOOL_MANAGED path automatically.
        argv.append("--no-ansi")
        if env:
            for key, value in env.items():
                # secret forwarding (e.g. PASSWORD) as an argv flag —
                # never printed, never shelled as a string (SKILL rule 5).
                argv += ["--env", f"{key}={value}"]

        return DriverCommand(argv=argv, automated=True, prompt=None)

    def drive(self, plan: ExecutionPlan) -> DriverResult:
        logcat_process = None
        if plan.capture is not None:
            # ALWAYS argv-list — never shell=True (SKILL rule 5).
            logcat_process = self._runner.start_capture(list(plan.capture.argv))

        logcat_lines: Sequence[str] = ()
        capture_failed = False
        diagnostics: str | None = None
        try:
            if plan.loop_mode == LoopMode.TOOL_MANAGED:
                iteration_outcomes, diagnostics = self._drive_tool_managed(plan)
            else:
                iteration_outcomes, diagnostics = self._drive_driver_managed(plan)
        finally:
            if logcat_process is not None:
                # Fix (resilience review): check the capture process's OWN
                # exit — a dead/failed logcat (e.g. multi-device error) must
                # be signalled distinctly from a healthy capture that simply
                # saw zero marker lines, never silently treated as the same
                # thing.
                capture_result = self._runner.stop_capture(logcat_process)
                logcat_lines = tuple(capture_result.lines)
                if capture_result.returncode not in (None, 0):
                    capture_failed = True
                    if diagnostics is None:
                        diagnostics = bounded_diagnostics("\n".join(capture_result.lines))

        argv_for_scrub = list(plan.command or plan.inner.argv or [])
        if diagnostics is not None:
            # Never let a forwarded secret (e.g. PASSWORD via --env) leak
            # into a failure diagnostic.
            diagnostics = scrub_secrets(diagnostics, argv_for_scrub)

        ok = bool(iteration_outcomes) and all(o == "ok" for o in iteration_outcomes)
        return DriverResult(
            ok=ok,
            iteration_outcomes=tuple(iteration_outcomes),
            logcat_lines=logcat_lines,
            capture_failed=capture_failed,
            diagnostics=diagnostics,
        )

    def _drive_tool_managed(self, plan: ExecutionPlan) -> tuple[list[str], str | None]:
        """Slice C (`run-live-progress` design): the single Flashlight-
        wrapped subprocess now streams LIVE through `run_streamed` (never
        `.run()`) so Flashlight's own stdout/stderr — and the nested Maestro
        output it wraps — relays to STDERR as it happens, exactly like
        `_drive_driver_managed`. Flashlight owns the iteration loop here
        (`--iterationCount`), so NO `iteration_started`/`iteration_finished`
        events are synthesized — there is exactly one subprocess and no
        real per-iteration boundary to bracket; every relayed line is
        treated as OPAQUE text, never parsed to fabricate one (design open
        item, confirmed against a live piped Maestro binary: plain text,
        no ANSI/cursor bytes).

        Cleanup fix (post-review): the framing header this method used to
        emit via `relayed_line` (indented like a nested tool line, and
        requiring a `_last_flow_name` field on this class to remember the
        flow name) moved OUT to `StderrProgressReporter.run_header()`
        (`cli/output/progress.py`), called by `cli/commands/run.py` BEFORE
        `execute()` — that call site already knows the flow name and
        iteration count without any adapter-side state."""
        if plan.command is None:
            raise RuntimeError("TOOL_MANAGED plan requires a composed command")
        result = self._runner.run_streamed(list(plan.command), on_line=self._reporter.relayed_line)
        outcome = "ok" if result.returncode == 0 else "failed"
        diagnostics = bounded_diagnostics(result.stderr) if result.returncode != 0 else None
        return [outcome] * plan.iterations, diagnostics

    def _drive_driver_managed(self, plan: ExecutionPlan) -> tuple[list[str], str | None]:
        """Slice B (`run-live-progress` design): each iteration streams LIVE
        through `run_streamed` (never `.run()` — that stays TOOL_MANAGED-
        only) so the caller sees Maestro's own step output as it happens,
        with `iteration_started`/`iteration_finished(ok=)` bracketing it for
        the live per-iteration table."""

        if plan.inner.argv is None:
            raise RuntimeError("DRIVER_MANAGED plan requires an automated inner command")
        outcomes: list[str] = []
        diagnostics: str | None = None
        total = plan.iterations
        for index in range(total):
            iteration_number = index + 1
            self._reporter.iteration_started(iteration_number, total)
            result = self._runner.run_streamed(
                list(plan.inner.argv), on_line=self._reporter.relayed_line
            )
            ok = result.returncode == 0
            outcomes.append("ok" if ok else "failed")
            self._reporter.iteration_finished(iteration_number, total, ok=ok)
            if not ok and diagnostics is None:
                # Keep the FIRST failure's stderr — enough to tell the user
                # which tool/flow/device failed and why (WARNING fix).
                diagnostics = bounded_diagnostics(result.stderr)
        return outcomes, diagnostics
