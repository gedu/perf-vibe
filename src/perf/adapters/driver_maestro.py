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

from typing import Mapping, Optional, Sequence, Tuple

from perf.adapters.process import SubprocessRunner, bounded_diagnostics, scrub_secrets
from perf.domain.model import DriverCommand, DriverResult, ExecutionPlan, LoopMode


class MaestroDriver:
    """`FlowDriver` (`domain/ports.py`) implementation."""

    def __init__(
        self,
        known_flows: Mapping[str, str],
        *,
        device: Optional[str] = None,
        runner: Optional[SubprocessRunner] = None,
    ) -> None:
        self._known_flows = dict(known_flows)
        self._device = device
        self._runner = runner if runner is not None else SubprocessRunner()

    def command(
        self,
        flow_name: str,
        *,
        mode: str,
        restart: bool,
        env: Optional[Mapping[str, str]] = None,
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
        diagnostics: Optional[str] = None
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

    def _drive_tool_managed(self, plan: ExecutionPlan) -> Tuple[list[str], Optional[str]]:
        if plan.command is None:
            raise RuntimeError("TOOL_MANAGED plan requires a composed command")
        result = self._runner.run(list(plan.command))
        outcome = "ok" if result.returncode == 0 else "failed"
        diagnostics = (
            bounded_diagnostics(result.stderr) if result.returncode != 0 else None
        )
        return [outcome] * plan.iterations, diagnostics

    def _drive_driver_managed(self, plan: ExecutionPlan) -> Tuple[list[str], Optional[str]]:
        if plan.inner.argv is None:
            raise RuntimeError("DRIVER_MANAGED plan requires an automated inner command")
        outcomes: list[str] = []
        diagnostics: Optional[str] = None
        for _ in range(plan.iterations):
            result = self._runner.run(list(plan.inner.argv))
            outcomes.append("ok" if result.returncode == 0 else "failed")
            if result.returncode != 0 and diagnostics is None:
                # Keep the FIRST failure's stderr — enough to tell the user
                # which tool/flow/device failed and why (WARNING fix).
                diagnostics = bounded_diagnostics(result.stderr)
        return outcomes, diagnostics
