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

`recap()` (Slice C, design "recap() placement"): an end-of-run summary from
a `RunFlowResult` — deliberately a method on THIS concrete reporter only,
never the pure `ProgressReporter` Protocol (`domain/ports.py`), since
`RunFlowResult` is an `application/` type. `cli/commands/run.py` calls it
directly on the concrete `StderrProgressReporter` it already retained (see
`build_progress_reporter`'s return type below), after `execute()` returns
successfully — never on a failure path (recap is skipped there; the
existing `emit_error` path is unchanged).

Data-availability note (design open item): `RunFlowResult.iteration_statuses`
is populated ONLY when the active `SystemSampler` surfaces true per-
iteration status (today: `FlashlightSampler`, from the results JSON's
`iterations[].status`). When it is empty/`None` (e.g. Maestro markers-only,
Manual, Replay — no Flashlight in play — OR a Flashlight report with zero
`iterations[]` entries) `recap()` renders an HONEST coverage summary from
`partial_coverage` instead of fabricating a per-iteration ✅ for iterations
it never actually observed. Correctness fix (post-review): an EMPTY list is
treated identically to `None` here — a truthy check, not an `is not None`
check — so a zero-entry report never silently renders NO coverage line at
all (only the header, no summary).

Header/row-count consistency (correctness fix, post-review): when a real
per-iteration table IS rendered, BOTH the header count and the row totals
are driven from the SAME number — `len(iteration_statuses)`, the ACTUAL
reported count — never the separately-tracked REQUESTED count
(`result.iterations`). If the two disagree (e.g. Flashlight excluded an
iteration from the report), the header says so honestly ("N requested · M
reported") instead of showing a number that would contradict the rows
below it.

`--quiet`/`-q` (Slice D, LOCKED decision: ONE flag = fully silent):
`build_progress_reporter(quiet=True)` returns `NullProgressReporter`
instead of `StderrProgressReporter` — mirrors `build_sampler` returning
`None` for "not selected" (design "`--quiet`" decision). Because
`cli/commands/run.py` calls `.recap()`/`.run_header()` on whatever this
factory returns, `NullProgressReporter` now ALSO implements both as true
no-ops (a full structural drop-in), and the factory's return type is
narrowed to the `CliProgressReporter` Protocol below — NOT the concrete
`StderrProgressReporter` it returned before Slice D — so mypy proves both
branches safe to call `.recap()`/`.run_header()` on without `run.py`
needing an `isinstance` guard or a `# type: ignore`.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Protocol, TextIO

if TYPE_CHECKING:
    from perf.application.run_flow import RunFlowResult

__all__ = [
    "CliProgressReporter",
    "NullProgressReporter",
    "StderrProgressReporter",
    "build_progress_reporter",
]

_PENDING = "⏳"
_OK = "✅"
_FAILED = "❌"

_BOLD_GREEN = "\x1b[1;32m"
_BOLD_RED = "\x1b[1;31m"
_RESET = "\x1b[0m"


class CliProgressReporter(Protocol):
    """Composition-root typing seam (Slice D): the domain `ProgressReporter`
    Protocol's 4 primitives PLUS the 2 concrete-only methods (`recap`,
    `run_header`) that `cli/commands/run.py` calls on whatever
    `build_progress_reporter` returns. Lives HERE, not `domain/ports.py` —
    `recap` needs `RunFlowResult`, an `application/` type the pure domain
    Protocol must never see (design "recap() placement").

    `StderrProgressReporter` and `NullProgressReporter` both satisfy this
    structurally; `build_progress_reporter`'s return type is THIS Protocol
    (not a `Union[...]`, not `# type: ignore`) so mypy proves both branches
    safe for `run.py` to call `.recap()`/`.run_header()` on without any
    `isinstance` guard."""

    def iteration_started(self, index: int, total: int) -> None: ...

    def iteration_finished(self, index: int, total: int, *, ok: bool) -> None: ...

    def awaiting_user_input(self, prompt: str) -> None: ...

    def relayed_line(self, text: str) -> None: ...

    def recap(self, result: RunFlowResult) -> None: ...

    def run_header(self, flow_name: str, iterations: int) -> None: ...


class NullProgressReporter:
    """`ProgressReporter` (`domain/ports.py`) no-op — the `--quiet`/`-q`
    reporter (Slice D) and a safe silent default. Every event is a no-op;
    it never touches any stream.

    Also implements `recap`/`run_header` (Slice D) as no-ops so it is a
    FULL drop-in for `StderrProgressReporter` from `run.py`'s point of
    view — a full structural match for `CliProgressReporter` above."""

    def iteration_started(self, index: int, total: int) -> None:
        pass

    def iteration_finished(self, index: int, total: int, *, ok: bool) -> None:
        pass

    def awaiting_user_input(self, prompt: str) -> None:
        pass

    def relayed_line(self, text: str) -> None:
        pass

    def recap(self, result: RunFlowResult) -> None:
        pass

    def run_header(self, flow_name: str, iterations: int) -> None:
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

    def recap(self, result: RunFlowResult) -> None:
        """End-of-run summary (Slice C) — renders ONCE, after `execute()`
        returns successfully, never interleaved with live progress (module
        docstring "Data-availability note"). Concrete-reporter-only, NOT on
        the `ProgressReporter` Protocol (design "recap() placement")."""
        statuses = result.iteration_statuses
        if statuses:
            # TRUE per-iteration status — only ever available when the
            # active `SystemSampler` surfaced it (today: `FlashlightSampler`
            # via the results JSON's `iterations[].status`). A truthy check
            # (not `is not None`) so an EMPTY list falls through to the
            # honest coarse summary below instead of silently rendering NO
            # coverage line (correctness fix, post-review).
            reported = len(statuses)
            if reported == result.iterations:
                count_label = f"{reported} iteration(s)"
            else:
                # Requested and reported counts disagree — never pick one
                # number that would contradict the rows rendered below;
                # say so honestly instead (correctness fix, post-review).
                count_label = f"{result.iterations} requested · {reported} reported"
            self._write(f"Recap · {result.flow_name} · {count_label}")
            for index, ok in enumerate(statuses, start=1):
                glyph = _OK if ok else _FAILED
                self._write(f"{glyph} iteration {index}/{reported}")
        else:
            # No per-iteration data to draw an honest table from (empty
            # list or `None`) — say so plainly using only the LOCKED
            # ⏳/✅/❌ vocabulary plus plain words, never a 4th glyph (spec
            # "Locked Emoji Status Vocabulary"; correctness fix, post-review
            # removed the non-locked ⚠️ glyph this branch used to emit).
            self._write(f"Recap · {result.flow_name} · {result.iterations} iteration(s)")
            if result.partial_coverage:
                # Never fabricate a ✅ for iterations never actually
                # observed (spec: never silently show all-✅ when coverage
                # was partial).
                self._write(
                    f"{_FAILED} {result.iterations} iteration(s) · partial coverage "
                    "(one or more iterations excluded)"
                )
            else:
                self._write(f"{_OK} {result.iterations} iteration(s) · complete")

    def run_header(self, flow_name: str, iterations: int) -> None:
        """Framing header for the TOOL_MANAGED (Maestro+Flashlight) path
        (`run-live-progress` cleanup fix, post-review): moved OUT of
        `driver_maestro.py`, which previously emitted it via `relayed_line`
        — 3-space-indented, so it read like a nested relayed tool line, and
        it forced a `_last_flow_name` mutable-state field onto the driver
        just to remember the flow name between `command()` and `drive()`.

        Concrete-reporter-only, same placement as `recap()` — NOT on the
        `ProgressReporter` Protocol (this needs no `application/` type, but
        is a CLI/composition-root presentation concern). `run.py` calls it
        directly, BEFORE `execute()`, only for the Flashlight-sampler
        TOOL_MANAGED path — it already knows `flow`/`resolved_iterations`
        without needing any adapter-side state."""
        self._write(f"🎯 {flow_name} · {iterations} iterations via Flashlight")

    def _write(self, line: str) -> None:
        self._stream.write(f"{line}\n")
        self._stream.flush()


def build_progress_reporter(
    *,
    quiet: bool = False,
    stderr_is_tty: bool = False,
    error_color_enabled: bool = False,
) -> CliProgressReporter:
    """Composition-root factory for the live `ProgressReporter` port.

    Lives in the CLI/presentation layer (NOT `adapters/registry.py`): a
    concrete reporter is purely a presentation concern, so building it here
    keeps the adapter layer free of any `cli/` import — the hexagonal
    direction is CLI -> adapters/domain, never the reverse (fixes the
    adapters->cli inversion + circular-import that a registry-side factory
    forced). `stderr_is_tty`/`error_color_enabled` mirror the SAME
    `OutputContext` fields resolved once by `resolve_output_context`, never
    re-derived from `isatty()` here.

    `quiet=True` (Slice D, LOCKED decision: ONE flag = fully silent) returns
    `NullProgressReporter` — mirrors `build_sampler` returning `None` for
    "not selected"; `stderr_is_tty`/`error_color_enabled` are simply unused
    in that branch, never threaded through.

    Returns the `CliProgressReporter` Protocol (this module's typing seam),
    NOT the concrete `StderrProgressReporter` it returned before Slice D:
    `cli/commands/run.py` calls `.recap()`/`.run_header()` on WHICHEVER
    reporter this returns, and since Slice D gives `NullProgressReporter`
    true no-op implementations of both, this Protocol is what proves both
    branches structurally safe to call — without an `isinstance` guard in
    `run.py` or a `# type: ignore` here. The narrower `ProgressReporter`
    Protocol (`domain/ports.py`) that `build_driver` accepts is still
    structurally satisfied by either concrete instance either way."""

    if quiet:
        return NullProgressReporter()
    return StderrProgressReporter(
        stderr_is_tty=stderr_is_tty,
        error_color_enabled=error_color_enabled,
    )
