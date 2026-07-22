"""Non-port process-spawn helper shared by `FlowDriver` adapters (design
§1: "The only shared adapter-internal code is a non-port
`adapters/process.py` helper (argv spawn + parallel capture) reused by
drivers — not a port, no domain impact").

ALWAYS `subprocess.run`/`subprocess.Popen` with an argv LIST — the `shell`
keyword is NEVER set truthy here, and no command is ever built by string
composition (SKILL rule 5). Real device/adb/maestro
processes are only ever spawned through this module; every adapter test
injects a fake runner instead (SKILL rule 8: "Adapters test against
recorded fixtures, not live devices").
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one `SubprocessRunner.run()` call."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CaptureResult:
    """Outcome of `SubprocessRunner.stop_capture()` (resilience fix): carries
    both the captured lines AND the capture process's exit code, so a
    DEAD/failed parallel capture (e.g. `adb logcat` exiting non-zero because
    of a multi-device error) can be distinguished from a healthy capture
    that simply observed zero lines. `returncode` is `None` only if the
    process could not report one at all."""

    lines: list
    returncode: Optional[int]


# Bound diagnostic text before it is ever surfaced to a user (never dump an
# unbounded subprocess stderr/output blob).
_MAX_DIAGNOSTICS_LENGTH = 2000


def bounded_diagnostics(text: str, *, max_len: int = _MAX_DIAGNOSTICS_LENGTH) -> Optional[str]:
    """Trim/bound raw stderr or captured-output text into a diagnostics
    string, or `None` when there is nothing to say."""

    stripped = text.strip()
    if not stripped:
        return None
    if len(stripped) > max_len:
        stripped = stripped[:max_len] + "... (truncated)"
    return stripped


def scrub_secrets(text: str, argv: Sequence[str]) -> str:
    """Redact any `--env KEY=VALUE` secret value found in `argv` (e.g.
    `PASSWORD`) from diagnostic text before it is ever surfaced — a failure
    message must never leak a forwarded secret (SKILL rule: never log
    secrets)."""

    scrubbed = text
    for index, token in enumerate(argv):
        if token == "--env" and index + 1 < len(argv):
            _, _, value = argv[index + 1].partition("=")
            if value:
                scrubbed = scrubbed.replace(value, "***")
    return scrubbed


class SubprocessRunner:
    """Default process runner — real `subprocess` calls. Tests inject a
    fake runner exposing the same `run`/`start_capture`/`stop_capture`
    surface instead of touching a live device."""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> CommandResult:
        completed = subprocess.run(
            list(argv),
            env=dict(env) if env is not None else None,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def start_capture(self, argv: Sequence[str]) -> subprocess.Popen:
        """Start a long-running argv-list process (e.g. `adb logcat`)
        whose stdout is captured in parallel with the drive step."""

        return subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def stop_capture(self, process: subprocess.Popen) -> CaptureResult:
        """Terminate a capture process started by `start_capture`, return
        every captured line AND the process's exit code (resilience fix:
        callers must be able to tell a dead/failed capture — e.g. `adb
        logcat`'s "more than one device" error — apart from a healthy
        capture that simply saw zero lines)."""

        process.terminate()
        try:
            stdout, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
        lines = stdout.splitlines() if stdout else []
        return CaptureResult(lines=lines, returncode=process.returncode)
