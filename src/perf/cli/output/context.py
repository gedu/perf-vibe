"""Shared TTY/color/`--json` resolution for the CLI (SKILL rule 6: honor
`--no-color` + `NO_COLOR` env + TTY detection; non-TTY stdout without
`--json` prints a one-line stderr nudge)."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO

__all__ = ["NON_TTY_NUDGE", "OutputContext", "resolve_output_context"]

NON_TTY_NUDGE = "note: non-terminal output detected — use --json for stable machine parsing"


@dataclass(frozen=True)
class OutputContext:
    """Resolved once per invocation (the CLI callback) and threaded to
    subcommands via `typer.Context.obj`."""

    json_mode: bool
    color_enabled: bool
    stdout_is_tty: bool
    # Error output goes to STDERR, whose TTY-ness is independent of stdout's
    # (e.g. `perfvibe run … > out.txt` pipes stdout but leaves stderr on the
    # terminal). `error_color_enabled` is therefore resolved separately so
    # errors stay colored in exactly that common case. Defaulted so existing
    # direct `OutputContext(...)` constructions keep working.
    stderr_is_tty: bool = False
    error_color_enabled: bool = False

    @property
    def should_nudge_stderr(self) -> bool:
        """One-line stderr nudge (SKILL rule 6) — only for the lossy pretty
        path on non-TTY stdout; `--json` output never needs it (it IS the
        stable machine contract already)."""

        return not self.json_mode and not self.stdout_is_tty


def resolve_output_context(
    *,
    json_mode: bool,
    no_color_cli: bool,
    stdout: TextIO,
    stderr: TextIO | None = None,
    no_color_config: bool = False,
    env: Mapping[str, str] | None = None,
) -> OutputContext:
    # Precedence (SKILL rule 6): CLI flag > NO_COLOR env > project/global config
    # > TTY default. `no_color_config` carries the resolved project/global
    # `no_color` setting so a `perf.toml` `no_color = true` actually disables
    # color (previously ignored). `stderr` defaults to `sys.stderr` so callers
    # that only care about stdout need not pass it.
    env = env if env is not None else os.environ
    stderr = stderr if stderr is not None else sys.stderr
    stdout_is_tty = bool(getattr(stdout, "isatty", lambda: False)())
    stderr_is_tty = bool(getattr(stderr, "isatty", lambda: False)())
    # The non-TTY-independent disablers apply identically to both streams;
    # only the TTY check differs per stream.
    disabled = no_color_cli or ("NO_COLOR" in env) or no_color_config
    color_enabled = stdout_is_tty and not disabled
    error_color_enabled = stderr_is_tty and not disabled
    return OutputContext(
        json_mode=json_mode,
        color_enabled=color_enabled,
        stdout_is_tty=stdout_is_tty,
        stderr_is_tty=stderr_is_tty,
        error_color_enabled=error_color_enabled,
    )
