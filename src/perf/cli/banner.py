"""Small ASCII-art `perf` banner (roadmap #45 — UX). Presentation-only,
HARD rules:
  - Shown ONLY on bare `perf` (no args) / `perf --help` — NEVER on any
    command's output (e.g. `perf run ...`).
  - Gated by TTY: NEVER printed into a pipe (`should_show_banner` returns
    `False` when stdout is not a TTY), independent of color.
  - Gated by `--no-color`/`NO_COLOR`: the banner text itself may still
    print on a TTY with color disabled, but NEVER wrapped in ANSI codes
    in that case.
  - NEVER printed in `--json` output or any data stream — the machine
    contract stays clean (`should_show_banner` returns `False` whenever
    `json_mode` is set).

No new dependency: plain stdlib string formatting, no `rich`/`textual`
TUI (roadmap #45: interactive mode is explicitly deferred).
"""

from __future__ import annotations

__all__ = ["render_banner", "should_show_banner"]

_BANNER_LINES: tuple[str, ...] = (
    "####  ##### ####  #####",
    "#   # #     #   # #",
    "####  ###   ####  ###",
    "#     #     #  #  #",
    "#     ##### #   # #",
)
_TAGLINE = "performance lab cli"

_CYAN = "\x1b[36m"
_RESET = "\x1b[0m"


def should_show_banner(*, json_mode: bool, stdout_is_tty: bool) -> bool:
    """The banner NEVER shows in `--json`/any data stream, and NEVER into
    a pipe — TTY-ness gates it independently of color settings."""

    return stdout_is_tty and not json_mode


def render_banner(*, color: bool) -> str:
    body = "\n".join(_BANNER_LINES)
    if color:
        body = f"{_CYAN}{body}{_RESET}"
    return f"{body}\n{_TAGLINE}\n"
