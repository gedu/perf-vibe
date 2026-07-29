"""Thin raw-terminal driver for the flow picker (POSIX `termios`/`tty`;
this tool targets macOS/Linux). It owns the ONLY side effects — putting the
terminal in raw mode, reading keystrokes off `sys.stdin`, and painting the
picker to `sys.stderr` (stdout stays reserved for results, SKILL rule 6).

The picker's decision logic is the PURE state machine in `flow_picker.py`;
this module just decodes raw bytes into that machine's logical keys, runs
the redraw loop, and restores the terminal on exit. `drive_picker` takes an
INJECTED key source and writer so the loop is exercised by scripted tests
with no real TTY (SKILL rule 3/8); only `pick_flows` touches `termios`, and
it raises `PickerUnavailable` when raw mode cannot be enabled so the caller
can fall back to the explicit usage-error path.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from perf.cli.output.flow_picker import (
    KEY_BACKSPACE,
    KEY_CTRL_A,
    KEY_DOWN,
    KEY_ENTER,
    KEY_ESC,
    KEY_TAB,
    KEY_UP,
    OUTCOME_ACCEPT,
    OUTCOME_CANCEL,
    apply_key,
    initial_state,
    render,
    resolved_selection,
)

__all__ = ["PickerUnavailable", "decode_key", "drive_picker", "pick_flows"]

_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"


class PickerUnavailable(Exception):
    """Raw mode could not be enabled (no controlling TTY, closed stdin, or a
    `termios` error). The caller falls back to the explicit `pass a flow name
    or --all` usage error."""


def decode_key(read: Callable[[int], str]) -> str:
    """Decode ONE logical keystroke from a blocking `read(n)` char source,
    parsing arrow escape sequences. Returns a `flow_picker` key token, a bare
    printable character, or `""` for an unrecognized/ignored key. EOF and
    Ctrl-C both cancel."""

    char = read(1)
    if char == "" or char == "\x03":  # EOF / Ctrl-C
        return KEY_ESC
    if char in ("\r", "\n"):
        return KEY_ENTER
    if char == "\t":
        return KEY_TAB
    if char == "\x01":
        return KEY_CTRL_A
    if char in ("\x7f", "\x08"):
        return KEY_BACKSPACE
    if char == "\x1b":
        # CSI arrow sequence (`ESC [ A/B`) or a lone Esc press.
        if read(1) == "[":
            return {"A": KEY_UP, "B": KEY_DOWN}.get(read(1), "")
        return KEY_ESC
    return char


def _repaint_prefix(prev_lines: int) -> str:
    # Move the cursor back to the top of the previous frame and clear
    # downward, so each redraw overwrites the last in place.
    if prev_lines <= 0:
        return ""
    if prev_lines == 1:
        return "\r\x1b[J"
    return f"\x1b[{prev_lines - 1}A\r\x1b[J"


def drive_picker(
    flows: tuple[str, ...],
    *,
    color: bool,
    read_key: Callable[[], str],
    write: Callable[[str], None],
    multi: bool = True,
) -> list[str] | None:
    """Run the picker loop against an injected key source and writer. Returns
    the chosen flow names on accept (Enter) or `None` on cancel (Esc/Ctrl-C).
    `multi=False` runs the single-select mode (`run` picks one flow). Pure
    w.r.t. the terminal — `pick_flows` supplies the real raw-mode I/O."""

    state = initial_state(flows, multi=multi)
    prev_lines = 0

    def paint() -> None:
        nonlocal prev_lines
        frame = render(state, color=color)
        write(_repaint_prefix(prev_lines) + frame)
        prev_lines = frame.count("\n") + 1

    paint()
    while True:
        state = apply_key(state, read_key())
        if state.outcome == OUTCOME_ACCEPT:
            return list(resolved_selection(state))
        if state.outcome == OUTCOME_CANCEL:
            return None
        paint()


def pick_flows(flows: tuple[str, ...], *, color: bool, multi: bool = True) -> list[str] | None:
    """Interactive entry point: put `sys.stdin` in raw mode, run the picker
    painting to `sys.stderr`, and always restore the terminal. `multi=False`
    runs the single-select mode (`run`). Raises `PickerUnavailable` if raw
    mode cannot be enabled."""

    import termios
    import tty

    stdin = sys.stdin
    try:
        fd = stdin.fileno()
        original = termios.tcgetattr(fd)
    except (termios.error, ValueError, OSError, AttributeError) as exc:
        raise PickerUnavailable(str(exc)) from exc

    def write(text: str) -> None:
        # Raw mode disables output post-processing (OPOST/ONLCR), so a bare
        # "\n" is a line-feed only — it drops a row without returning to
        # column 0. Translate to CRLF ourselves; otherwise each frame line
        # staircases rightward and the cursor-up repaint math drifts, leaving
        # stale header lines stacked on screen. The pure `render` keeps "\n".
        sys.stderr.write(text.replace("\n", "\r\n"))
        sys.stderr.flush()

    try:
        tty.setraw(fd)
        write(_HIDE_CURSOR)
        return drive_picker(
            flows,
            color=color,
            read_key=lambda: decode_key(stdin.read),
            write=write,
            multi=multi,
        )
    except KeyboardInterrupt:
        # ISIG is off in raw mode so this is belt-and-suspenders; a Ctrl-C
        # that still lands is a cancel, never a traceback.
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
        write(_SHOW_CURSOR + "\n")
