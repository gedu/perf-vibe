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

import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


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
    returncode: int | None


# Bound diagnostic text before it is ever surfaced to a user (never dump an
# unbounded subprocess stderr/output blob).
_MAX_DIAGNOSTICS_LENGTH = 2000

# When bounding exceeds the limit, split the AVAILABLE budget between head
# and tail rather than keeping only the head. A verbose tool's real failure
# line (a Maestro assertion, an `ENOENT`, `exited with code 1`) is very often
# at the TAIL of its output — a head-only trim (the previous behavior)
# silently dropped it, leaving `salient_tool_line` nothing to find but the
# first, usually boring, line (e.g. "Running on Pixel_8_Pro"). The head/tail
# sizes are DERIVED from the caller's `max_len` (see `bounded_diagnostics`
# below) rather than fixed constants, so the bound is actually honored for
# any `max_len`, not just the module default.
_TRUNCATION_MARKER_HEAD = "\n... (truncated "
_TRUNCATION_MARKER_TAIL = " chars) ...\n"


def bounded_diagnostics(text: str, *, max_len: int = _MAX_DIAGNOSTICS_LENGTH) -> str | None:
    """Trim/bound raw stderr or captured-output text into a diagnostics
    string, or `None` when there is nothing to say.

    When the text exceeds `max_len`, keeps BOTH the head and the tail (with a
    `... (truncated N chars) ...` marker in between) instead of dropping the
    tail entirely — the tail is where a real tool failure line usually lives
    (see the module comment above). The head/tail sizes are DERIVED from
    `max_len` itself (split the remaining budget after reserving room for the
    marker) so the returned text — marker included — never exceeds `max_len`,
    and the "truncated N chars" count is always accurate and non-negative,
    for ANY `max_len`."""

    stripped = text.strip()
    if not stripped:
        return None
    if len(stripped) <= max_len:
        return stripped

    # Reserve room for the marker using the WORST-CASE digit count the
    # omitted-chars number could ever need: `omitted` is always <=
    # len(stripped), so it can never need more digits than len(stripped)
    # itself does.
    max_digits = len(str(len(stripped)))
    marker_overhead = len(_TRUNCATION_MARKER_HEAD) + max_digits + len(_TRUNCATION_MARKER_TAIL)
    body_budget = max(0, max_len - marker_overhead)
    head_len = body_budget // 2
    tail_len = body_budget - head_len

    head = stripped[:head_len]
    tail = stripped[-tail_len:] if tail_len else ""
    omitted = len(stripped) - head_len - tail_len
    marker = f"{_TRUNCATION_MARKER_HEAD}{omitted}{_TRUNCATION_MARKER_TAIL}"
    return f"{head}{marker}{tail}"


# `run_streamed`'s OWN defensive cap on its accumulated `CommandResult` text —
# independent from (and much larger than) `bounded_diagnostics`' 2000-char
# DISPLAY trim, which callers still apply on top for anything actually shown
# to a user. This only guards against unbounded memory growth on a very
# chatty/long-lived child process; it never changes the live per-line relay,
# which sees every line regardless.
_MAX_STREAM_BUFFER_CHARS = 50_000

# Matches a forwarded `--env KEY=VALUE` secret assignment anywhere in the
# joined argv — including when it is NESTED inside a single token such as
# Flashlight's `--testCommand "maestro test <flow> --env PASSWORD=..."` string
# (the TOOL_MANAGED path), not only as two standalone `--env` / `KEY=VALUE`
# tokens (the DRIVER_MANAGED path). `\S+?` is the key, group(1) the value.
_ENV_SECRET_RE = re.compile(r"--env\s+\S+?=(\S+)")


def scrub_secrets(text: str, argv: Sequence[str]) -> str:
    """Redact any `--env KEY=VALUE` secret value carried in `argv` (e.g.
    `PASSWORD`) from diagnostic text before it is ever surfaced — a failure
    message must never leak a forwarded secret (SKILL rule: never log
    secrets). Scans the JOINED argv so a value nested inside a `--testCommand`
    string is redacted just like a standalone token."""

    scrubbed = text
    haystack = " ".join(argv)
    for value in _ENV_SECRET_RE.findall(haystack):
        if value:
            scrubbed = scrubbed.replace(value, "***")
    return scrubbed


class SubprocessRunner:
    """Default process runner — real `subprocess` calls. Tests inject a
    fake runner exposing the same `run`/`run_streamed`/`start_capture`/
    `stop_capture` surface instead of touching a live device."""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
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

    def run_streamed(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Like `run()`, but relays each output line to `on_line` LIVE as the
        child produces it (`run-live-progress` design "Relay mechanism" —
        drivers pass `on_line=reporter.relayed_line`), instead of buffering
        everything until the process exits. `.run()` itself is left
        completely untouched — its 10 existing buffered callers keep their
        exact current behavior; this is a NEW, separate call site.

        `stderr=subprocess.STDOUT` merges both streams into ONE pipe (same
        choice as `start_capture`) so a single blocking read loop can never
        deadlock on two undrained pipes, and so interleaving matches what a
        user watching the terminal directly would see.

        Every line is scrubbed with `scrub_secrets(line, argv)` BEFORE it
        reaches `on_line` AND before it joins the accumulated buffer — a
        forwarded secret (e.g. `--env PASSWORD=...`) must never reach the
        live relay either (design threat matrix: "Secret in streamed
        output").

        Returns the SAME `CommandResult` shape as `.run()`, with the merged,
        scrubbed, bounded text in BOTH `stdout` and `stderr` so a caller's
        existing `bounded_diagnostics(result.stderr)` failure path keeps
        working unchanged.
        """

        argv_list = list(argv)
        process = subprocess.Popen(
            argv_list,
            env=dict(env) if env is not None else None,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        accumulated: list[str] = []
        accumulated_len = 0
        assert process.stdout is not None  # guaranteed by stdout=PIPE above
        for raw_line in process.stdout:
            # `raw_line` keeps its trailing line terminator (or none, for a
            # final partial line) — strip only the terminator so a `\r` used
            # mid-line (e.g. a progress-bar redraw) is preserved verbatim
            # rather than silently dropped.
            line = raw_line.rstrip("\n")
            scrubbed = scrub_secrets(line, argv_list)
            if on_line is not None:
                on_line(scrubbed)
            if accumulated_len < _MAX_STREAM_BUFFER_CHARS:
                accumulated.append(scrubbed)
                accumulated_len += len(scrubbed) + 1
        process.stdout.close()
        returncode = process.wait()

        merged = "\n".join(accumulated)
        return CommandResult(returncode=returncode, stdout=merged, stderr=merged)

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
