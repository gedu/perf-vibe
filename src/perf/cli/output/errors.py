"""Presentation-layer error rendering for the CLI — ONE red/bold `Error:`
renderer that every command routes its stderr through, plus salient-line +
hint extraction so a noisy multi-line tool stderr (e.g. Flashlight dumping a
Node stack trace on failure) surfaces the ONE line that matters and an
actionable next step, instead of 30 lines of framework internals.

PURE presentation, mirroring the rest of `cli/output/`:
- `render_error` builds the string and never does I/O (directly testable).
- `salient_tool_line`/`hint_for_diagnostics` operate on the ALREADY bounded,
  ALREADY secret-scrubbed diagnostics string the use-case/adapters produced
  (`adapters/process.py`) — they add no new I/O and import no domain/adapter.
- `emit_error` is the thin `typer.echo(..., err=True)` wrapper the commands
  call; it honors the resolved STDERR color decision (`OutputContext.
  error_color_enabled`), distinct from the stdout decision so piping stdout
  to a file still colors errors on an interactive stderr.

Backtick spans in a message/hint (`` `--force` ``, `` `adb devices` ``) are
bolded when color is on and unwrapped to plain text when it is off — so the
no-color output stays byte-clean (a literal backtick never leaks through) and
existing plain-text assertions keep matching.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from perf.cli.output.context import OutputContext

__all__ = [
    "emit_error",
    "emit_warning",
    "hint_for_diagnostics",
    "render_error",
    "render_warning",
    "salient_tool_line",
]

_RED_BOLD = "\x1b[1;31m"
_YELLOW_BOLD = "\x1b[1;33m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"

_BACKTICK_RE = re.compile(r"`([^`]+)`")


def _emphasize(text: str, *, color: bool) -> str:
    if color:
        return _BACKTICK_RE.sub(lambda m: f"{_BOLD}{m.group(1)}{_RESET}", text)
    return _BACKTICK_RE.sub(lambda m: m.group(1), text)


def render_error(
    message: str,
    *,
    color: bool,
    cause: str | None = None,
    hint: str | None = None,
) -> str:
    """Render one CLI error block. `cause`/`hint` are optional follow-up
    lines (used mainly by `run`'s device-failure path); when both are
    absent the output is exactly `Error: <message>` (color aside), keeping
    it a drop-in for the previous plain echoes."""

    prefix = f"{_RED_BOLD}Error:{_RESET}" if color else "Error:"
    lines = [f"{prefix} {_emphasize(message, color=color)}"]
    if cause:
        label = f"{_DIM}cause:{_RESET}" if color else "cause:"
        lines.append(f"  {label} {cause}")
    if hint:
        label = f"{_DIM}hint:{_RESET}" if color else "hint:"
        lines.append(f"  {label} {_emphasize(hint, color=color)}")
    return "\n".join(lines)


def render_warning(message: str, *, color: bool) -> str:
    """One-line `warning:` block (yellow when color is on) for a NON-fatal
    issue the user should still SEE — e.g. a run that persisted samples but a
    configured marker source captured zero/partial markers. Distinct from
    `render_error`: it never implies failure and never changes the exit code.
    Backtick spans are emphasized/unwrapped exactly like errors."""

    prefix = f"{_YELLOW_BOLD}warning:{_RESET}" if color else "warning:"
    return f"{prefix} {_emphasize(message, color=color)}"


# Lines we never want to surface as the human-facing "cause": Node/JS stack
# frames, the `status/signal/output` object dump, and the raw byte-Buffer
# array a tool like Flashlight prints when a child process fails.
_NOISE_RE = re.compile(
    r"^(?:at\s|node:internal|const\s|\^|\}|\{|status:|signal:|output:|Buffer\(|null,?$|\]|\[|\d+,)"
)


# Real failure signals to look for AFTER the `adb:`/specific-`Error:` tiers
# and BEFORE falling back to line one — most tools' verbose framing (a
# Maestro step-by-step log, a device-info banner) sorts earlier than the
# line that actually says what broke. Order is most-specific-first; the
# first pattern with a match wins.
_FAILURE_SIGNAL_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"ENOENT|no such file", re.IGNORECASE),
    re.compile(r"exited with code"),
    re.compile(r"Iteration\s+\d+/\d+\s+failed|🚨"),
    re.compile(r"Assertion\b.*failed", re.IGNORECASE),
    re.compile(r"\.\.\.\s*FAILED\b|\bFAILED\b"),
)


def salient_tool_line(diagnostics: str | None) -> str | None:
    """Pick the single most meaningful line out of a (possibly huge,
    multi-line) tool stderr. Prefers an explicit `adb:` line, then a
    specific `Error:` line, then a recognized failure signal (`ENOENT`,
    `exited with code`, `Iteration N/M failed`/🚨, `Assertion ... failed`,
    `... FAILED`), skipping Node/JS stack frames and the binary Buffer
    dump. Falls back to the first meaningful line only when nothing more
    specific is found — a device-info banner ("Running on Pixel_8_Pro")
    must never win over a real failure line further down. Returns `None`
    when there is nothing meaningful to show."""

    if not diagnostics:
        return None

    meaningful = [
        line
        for raw in diagnostics.splitlines()
        if (line := raw.strip()) and not _NOISE_RE.match(line)
    ]
    if not meaningful:
        return None

    for line in meaningful:
        if line.startswith("adb:"):
            return line
    for line in meaningful:
        # A specific error beats the generic "Error: Command failed: <cmd>".
        if line.startswith("Error:") and "Command failed" not in line:
            return line[len("Error:") :].strip() or line
    for pattern in _FAILURE_SIGNAL_RES:
        for line in meaningful:
            if pattern.search(line):
                return line
    return meaningful[0]


# Ordered most-specific-first: the first pattern that matches the raw
# diagnostics wins. Hints reference the command the user should run next.
_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"device unauthorized", re.IGNORECASE),
        "accept the USB-debugging dialog on your device, then run `adb kill-server && adb devices`",
    ),
    (
        re.compile(r"more than one device", re.IGNORECASE),
        "multiple devices are connected — pin one with `--device <serial>` "
        "(list them with `adb devices`)",
    ),
    (
        # `device not found` may carry the serial in between
        # (`device 'emulator-5554' not found`), so allow anything up to the
        # end of the line between "device" and "not found".
        re.compile(
            r"no devices?/emulators?|device offline|device\b[^\n]*\bnot found",
            re.IGNORECASE,
        ),
        "no usable device detected — connect one and check `adb devices`",
    ),
    (
        # `command not found` (shell) OR a missing-BINARY-style ENOENT: a
        # Python subprocess-spawn `FileNotFoundError` reads
        # `No such file or directory: '<name>'` where the quoted target has
        # NO `/` path separator (e.g. `'flashlight'`). Deliberately does NOT
        # match a generic file/dir ENOENT (e.g. `open 'results/foo.json'`,
        # quoted target HAS a `/`) — that is a real path problem, not a
        # missing tool, and the "missing from PATH" hint would be actively
        # misleading there (no hint is better than a wrong one).
        re.compile(
            r"command not found|no such file or directory:\s*'[^'/]*'",
            re.IGNORECASE,
        ),
        "a required tool (maestro / flashlight / adb) is missing from PATH — install it and retry",
    ),
)


def hint_for_diagnostics(diagnostics: str | None) -> str | None:
    """Map a known failure signature in the raw diagnostics to a short,
    actionable next step, or `None` when nothing recognizable matches."""

    if not diagnostics:
        return None
    for pattern, hint in _HINTS:
        if pattern.search(diagnostics):
            return hint
    return None


def emit_error(
    output: OutputContext,
    message: str,
    *,
    cause: str | None = None,
    hint: str | None = None,
) -> None:
    """`typer.echo` an `Error:` block to stderr, colored per the resolved
    STDERR decision. The single call site pattern every command uses."""

    typer.echo(
        render_error(message, color=output.error_color_enabled, cause=cause, hint=hint),
        err=True,
    )


def emit_warning(output: OutputContext, message: str) -> None:
    """`typer.echo` a `warning:` block to stderr, colored per the resolved
    STDERR decision. NEVER affects the exit code — a warning is not a
    failure. Shown regardless of `--quiet` (a warning is not progress chrome
    the user asked to silence)."""

    typer.echo(render_warning(message, color=output.error_color_enabled), err=True)
