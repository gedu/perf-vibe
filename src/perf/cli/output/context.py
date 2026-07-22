"""Shared TTY/color/`--json` resolution for the CLI (SKILL rule 6: honor
`--no-color` + `NO_COLOR` env + TTY detection; non-TTY stdout without
`--json` prints a one-line stderr nudge)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional, TextIO

__all__ = ["OutputContext", "resolve_output_context", "NON_TTY_NUDGE"]

NON_TTY_NUDGE = "note: non-terminal output detected — use --json for stable machine parsing"


@dataclass(frozen=True)
class OutputContext:
    """Resolved once per invocation (the CLI callback) and threaded to
    subcommands via `typer.Context.obj`."""

    json_mode: bool
    color_enabled: bool
    stdout_is_tty: bool

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
    no_color_config: bool = False,
    env: Optional[Mapping[str, str]] = None,
) -> OutputContext:
    # Precedence (SKILL rule 6): CLI flag > NO_COLOR env > project/global config
    # > TTY default. `no_color_config` carries the resolved project/global
    # `no_color` setting so a `perf.toml` `no_color = true` actually disables
    # color (previously ignored).
    env = env if env is not None else os.environ
    stdout_is_tty = bool(getattr(stdout, "isatty", lambda: False)())
    no_color_env = "NO_COLOR" in env
    color_enabled = (
        stdout_is_tty and not no_color_cli and not no_color_env and not no_color_config
    )
    return OutputContext(
        json_mode=json_mode,
        color_enabled=color_enabled,
        stdout_is_tty=stdout_is_tty,
    )
