"""`FlowDriver` port adapter — replay (device-free demos/testing).

Replays a RECORDED capture (a logcat text file + an optional Flashlight
results JSON) through the REAL production pipeline — marker parse,
Flashlight parse, `SqliteStore`, confirmation output — with no device, no
`adb`/`maestro`/`flashlight` subprocess, and no network I/O. This lets `perf
run` be demonstrated and exercised end-to-end without a physical/emulated
device attached.

`command()` returns a non-`None` `argv` (unlike `ManualDriver`) so a
configured `FlashlightSampler.wrap()` actually wraps the inner command and
produces a `results_path` — `FlashlightSampler.wrap()` returns `None` when
`inner.argv is None`, which would silently skip Flashlight-sample replay
entirely.

`drive()` never spawns a process: it copies the Flashlight fixture (when
configured) to `plan.results_path` so `SystemSampler.parse()` reads it
verbatim, and reads the logcat fixture into lines so `MarkerSource.parse()`
sees the same recorded markers on every replay.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

from perf.domain.model import DriverCommand, DriverResult, ExecutionPlan


class ReplayDriver:
    """`FlowDriver` (`domain/ports.py`) implementation."""

    def __init__(
        self,
        *,
        logcat_path: str | Path,
        flashlight_path: str | Path | None = None,
    ) -> None:
        self._logcat_path = Path(logcat_path)
        self._flashlight_path = Path(flashlight_path) if flashlight_path is not None else None

    def command(
        self,
        flow_name: str,
        *,
        mode: str,
        restart: bool,
        env: Mapping[str, str] | None = None,
    ) -> DriverCommand:
        del mode, restart, env  # replay ignores these — the capture is fixed
        return DriverCommand(argv=["replay", flow_name], automated=True, prompt=None)

    def drive(self, plan: ExecutionPlan) -> DriverResult:
        if plan.results_path is not None and self._flashlight_path is not None:
            target = Path(plan.results_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self._flashlight_path, target)

        logcat_lines = tuple(self._logcat_path.read_text().splitlines())

        return DriverResult(
            ok=True,
            iteration_outcomes=("ok",) * plan.iterations,
            logcat_lines=logcat_lines,
            capture_failed=False,
        )
