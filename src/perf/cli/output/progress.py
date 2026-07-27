"""Concrete `ProgressReporter` (`domain/ports.py`) implementations
(`run-live-progress` design "a concrete StderrProgressReporter (cli/output/)
owns all ANSI/TTY rendering on STDERR"). Injected INTO driver adapters
(built by the CLI composition root via `build_progress_reporter` below —
NOT by `adapters/registry.py`, so the adapter layer never imports `cli/`),
never into `RunFlowUseCase` — this module owns ONLY presentation, never
drive-loop logic.

HAND-ROLLED ANSI, NOT `rich` (perf-cli-standards rule 9 / python-architecture
"don't build for platforms you aren't shipping" — first-use unjustified);
same local-palette-constant idiom as `cli/output/budget_check_pretty.py`.

Emoji vocabulary is LOCKED to exactly three glyphs (spec 'Locked Emoji
Status Vocabulary'): ⏳ pending/running, ✅ ok, ❌ failed. No driver may
substitute an alternate symbol — every driver goes through THIS module.

Rendering model: APPEND-ONLY SEQUENTIAL, identically in TTY and non-TTY
mode (only color differs, gated on `error_color_enabled`) — NO cursor
control byte is ever emitted, in either mode. This replaces an earlier
in-place redraw table that tracked how many rows it had painted and moved
the cursor up by that count before repainting: `relayed_line()` prints a
line WITHOUT going through that bookkeeping, and in production
(`driver_maestro.py` `_drive_driver_managed`) relayed step lines are
emitted BETWEEN `iteration_started` and `iteration_finished` — so the next
redraw's cursor-up count went stale and clobbered the wrong terminal rows
on every driver-managed TTY run that relayed any step output (the norm).
An in-place table cannot coexist with live scrolling relay without a full
TUI library, and `rich` is out of scope (SKILL rule 9) — so the redraw
machinery is removed entirely rather than patched.

`recap()` (end-of-run summary from a `RunFlowResult`) is deliberately NOT
implemented here yet — that is Slice C's job (it needs an `application/`
type that must never reach the pure `ProgressReporter` Protocol).
"""

from __future__ import annotations

import sys
from typing import TextIO

from perf.domain.ports import ProgressReporter

__all__ = ["NullProgressReporter", "StderrProgressReporter", "build_progress_reporter"]

_PENDING = "⏳"
_OK = "✅"
_FAILED = "❌"

_BOLD_GREEN = "\x1b[1;32m"
_BOLD_RED = "\x1b[1;31m"
_RESET = "\x1b[0m"


class NullProgressReporter:
    """`ProgressReporter` (`domain/ports.py`) no-op — the `--quiet`/`-q`
    reporter (Slice D) and a safe silent default. Every event is a no-op;
    it never touches any stream."""

    def iteration_started(self, index: int, total: int) -> None:
        pass

    def iteration_finished(self, index: int, total: int, *, ok: bool) -> None:
        pass

    def awaiting_user_input(self, prompt: str) -> None:
        pass

    def relayed_line(self, text: str) -> None:
        pass


class StderrProgressReporter:
    """Concrete `ProgressReporter` (`domain/ports.py`) — owns ALL rendering
    on STDERR. Append-only sequential: every event writes exactly ONE line
    and TTY/non-TTY render IDENTICALLY, differing only in whether the
    finished-iteration line is wrapped in color — no cursor-control byte is
    ever emitted, in either mode (see module docstring for why the earlier
    in-place redraw table was removed)."""

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        stderr_is_tty: bool = False,
        error_color_enabled: bool = False,
    ) -> None:
        self._stream: TextIO = stream if stream is not None else sys.stderr
        # Retained for API stability (constructor signature must not change
        # so `run.py`/`build_progress_reporter` keep working unmodified) and
        # for potential future use — the append-only renderer does not
        # currently branch on it, since TTY and non-TTY output are now
        # identical apart from color.
        self._stderr_is_tty = stderr_is_tty
        self._color = error_color_enabled

    def iteration_started(self, index: int, total: int) -> None:
        self._write(f"{_PENDING} iteration {index}/{total}")

    def iteration_finished(self, index: int, total: int, *, ok: bool) -> None:
        glyph = _OK if ok else _FAILED
        text = f"{glyph} iteration {index}/{total}"
        if self._color:
            text = f"{_BOLD_GREEN if ok else _BOLD_RED}{text}{_RESET}"
        self._write(text)

    def awaiting_user_input(self, prompt: str) -> None:
        self._write(prompt)

    def relayed_line(self, text: str) -> None:
        # Indented 3 spaces to nest visually under the current iteration —
        # never re-scrub here, `run_streamed` already scrubbed secrets
        # before relaying (`adapters/process.py`).
        self._write(f"   {text}")

    def _write(self, line: str) -> None:
        self._stream.write(f"{line}\n")
        self._stream.flush()


def build_progress_reporter(
    *,
    stderr_is_tty: bool = False,
    error_color_enabled: bool = False,
) -> ProgressReporter:
    """Composition-root factory for the live `ProgressReporter` port.

    Lives in the CLI/presentation layer (NOT `adapters/registry.py`): a
    concrete reporter is purely a presentation concern, so building it here
    keeps the adapter layer free of any `cli/` import — the hexagonal
    direction is CLI -> adapters/domain, never the reverse (fixes the
    adapters->cli inversion + circular-import that a registry-side factory
    forced). `stderr_is_tty`/`error_color_enabled` mirror the SAME
    `OutputContext` fields resolved once by `resolve_output_context`, never
    re-derived from `isatty()` here. Slice D adds a `quiet` path returning
    `NullProgressReporter` (mirrors `build_sampler` returning `None`)."""

    return StderrProgressReporter(
        stderr_is_tty=stderr_is_tty,
        error_color_enabled=error_color_enabled,
    )
