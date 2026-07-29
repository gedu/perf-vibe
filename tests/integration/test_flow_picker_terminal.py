"""Driver-level tests for the raw-terminal flow picker
(`cli/output/flow_picker_terminal.py`). The termios/tty raw-mode setup is
the ONLY real side effect and is NOT exercised here (it needs a live TTY —
SKILL rule 7: never a real device/terminal in a test); instead the driver's
key-source and writer are INJECTED so scripted keystrokes drive the exact
same loop a real terminal would (SKILL rule 3: exercise the real loop, fake
only the boundary). Also unit-tests the escape-sequence decoder directly.
"""

from __future__ import annotations

from perf.cli.output.flow_picker import (
    KEY_BACKSPACE,
    KEY_CTRL_A,
    KEY_DOWN,
    KEY_ENTER,
    KEY_ESC,
    KEY_TAB,
    KEY_UP,
)
from perf.cli.output.flow_picker_terminal import decode_key, drive_picker

FLOWS = ("checkout", "login", "search", "settings")


class _ScriptedReader:
    """A blocking read(n) over a fixed byte-string script — stands in for a
    raw-mode `sys.stdin.read` without a real terminal."""

    def __init__(self, script: str) -> None:
        self._buf = script
        self._pos = 0

    def read(self, count: int) -> str:
        chunk = self._buf[self._pos : self._pos + count]
        self._pos += len(chunk)
        return chunk


def _key_source(keys):
    it = iter(keys)
    return lambda: next(it)


def test_drive_picker_enter_on_highlight_returns_single_flow():
    writes: list[str] = []
    selection = drive_picker(
        FLOWS,
        color=False,
        read_key=_key_source([KEY_DOWN, KEY_ENTER]),
        write=writes.append,
    )
    assert selection == ["login"]
    # It painted at least the initial frame plus one redraw.
    assert any("login" in frame for frame in writes)


def test_drive_picker_tab_multi_select_then_enter_returns_all_toggled():
    selection = drive_picker(
        FLOWS,
        color=False,
        read_key=_key_source([KEY_TAB, KEY_DOWN, KEY_DOWN, KEY_TAB, KEY_ENTER]),
        write=lambda _s: None,
    )
    assert selection == ["checkout", "search"]


def test_drive_picker_filter_then_ctrl_a_selects_visible():
    selection = drive_picker(
        FLOWS,
        color=False,
        read_key=_key_source(["s", "e", KEY_CTRL_A, KEY_ENTER]),
        write=lambda _s: None,
    )
    assert selection == ["search", "settings"]


def test_drive_picker_backspace_edits_filter():
    selection = drive_picker(
        FLOWS,
        color=False,
        read_key=_key_source(["l", "o", "g", KEY_BACKSPACE, KEY_BACKSPACE, KEY_ENTER]),
        write=lambda _s: None,
    )
    # "l" matches only "login" -> highlighted -> Enter runs it.
    assert selection == ["login"]


def test_drive_picker_single_mode_enter_returns_highlighted_and_ignores_tab():
    selection = drive_picker(
        FLOWS,
        color=False,
        # Tab is a no-op in single mode; the arrow+Enter picks one flow.
        read_key=_key_source([KEY_TAB, KEY_DOWN, KEY_ENTER]),
        write=lambda _s: None,
        multi=False,
    )
    assert selection == ["login"]


def test_drive_picker_esc_cancels_returns_none():
    selection = drive_picker(
        FLOWS,
        color=False,
        read_key=_key_source([KEY_ESC]),
        write=lambda _s: None,
    )
    assert selection is None


def test_decode_key_maps_arrows_from_escape_sequences():
    assert decode_key(_ScriptedReader("\x1b[A").read) == KEY_UP
    assert decode_key(_ScriptedReader("\x1b[B").read) == KEY_DOWN


def test_decode_key_maps_control_keys():
    assert decode_key(_ScriptedReader("\r").read) == KEY_ENTER
    assert decode_key(_ScriptedReader("\n").read) == KEY_ENTER
    assert decode_key(_ScriptedReader("\t").read) == KEY_TAB
    assert decode_key(_ScriptedReader("\x01").read) == KEY_CTRL_A
    assert decode_key(_ScriptedReader("\x7f").read) == KEY_BACKSPACE
    assert decode_key(_ScriptedReader("\x03").read) == KEY_ESC  # Ctrl-C cancels
    assert decode_key(_ScriptedReader("\x1b").read) == KEY_ESC  # lone Esc


def test_decode_key_passes_printable_through():
    assert decode_key(_ScriptedReader("a").read) == "a"


def test_decode_key_eof_is_cancel():
    assert decode_key(_ScriptedReader("").read) == KEY_ESC
