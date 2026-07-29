"""PURE fzf-style flow-picker state machine — the interactive `perfvibe
compare` flow selector's logic, split from all terminal I/O so it is fully
unit-testable (SKILL rule 8: every side effect is behind a seam; this module
has none). The thin raw-terminal driver that feeds real keystrokes into
`apply_key` and paints `render` lives in `flow_picker_terminal.py`.

The state is an immutable snapshot; `apply_key(state, key)` returns the NEXT
snapshot for one logical keystroke, and `render(state)` produces the stderr
view. Keys arrive already decoded into the logical tokens below (the driver
owns escape-sequence parsing), so this module never touches raw bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

__all__ = [
    "KEY_BACKSPACE",
    "KEY_CTRL_A",
    "KEY_DOWN",
    "KEY_ENTER",
    "KEY_ESC",
    "KEY_TAB",
    "KEY_UP",
    "OUTCOME_ACCEPT",
    "OUTCOME_CANCEL",
    "PickerState",
    "apply_key",
    "initial_state",
    "render",
    "resolved_selection",
    "visible",
]

# Logical key tokens the driver decodes raw input into. A single printable
# character is passed through verbatim (it edits the filter); anything else
# is one of these multi-character tokens or is ignored.
KEY_UP = "up"
KEY_DOWN = "down"
KEY_TAB = "tab"
KEY_CTRL_A = "ctrl-a"
KEY_ENTER = "enter"
KEY_ESC = "esc"
KEY_BACKSPACE = "backspace"

OUTCOME_ACCEPT = "accept"
OUTCOME_CANCEL = "cancel"

_BOLD = "\x1b[1m"
_REVERSE = "\x1b[7m"
_RESET = "\x1b[0m"

_HEADER = "Select flows — type to filter, ↑/↓ move, Tab select, Ctrl-A all, Enter run, Esc cancel"


@dataclass(frozen=True)
class PickerState:
    """One immutable snapshot of the picker. `flows` is the full, ordered
    candidate set (config-known flows, sorted by the caller); `selected` is
    by NAME so a toggle survives the flow being filtered off-screen."""

    flows: tuple[str, ...]
    query: str = ""
    cursor: int = 0
    selected: frozenset[str] = frozenset()
    outcome: str | None = None


def initial_state(flows: tuple[str, ...]) -> PickerState:
    return PickerState(flows=tuple(flows))


def visible(state: PickerState) -> tuple[str, ...]:
    """The flows matching the current filter — a case-insensitive substring
    match. An empty query shows every flow."""

    if not state.query:
        return state.flows
    needle = state.query.lower()
    return tuple(flow for flow in state.flows if needle in flow.lower())


def _clamp_cursor(cursor: int, count: int) -> int:
    if count == 0:
        return 0
    return max(0, min(cursor, count - 1))


def _with_query(state: PickerState, query: str) -> PickerState:
    # Re-derive the visible list under the new query and clamp the cursor so
    # it never dangles past a shrunken list (spec 'cursor clamps when list
    # shrinks').
    candidate = replace(state, query=query)
    return replace(candidate, cursor=_clamp_cursor(state.cursor, len(visible(candidate))))


def _move(state: PickerState, delta: int) -> PickerState:
    count = len(visible(state))
    return replace(state, cursor=_clamp_cursor(state.cursor + delta, count))


def _toggle(state: PickerState) -> PickerState:
    rows = visible(state)
    if not rows:
        return state
    highlighted = rows[state.cursor]
    selected = set(state.selected)
    if highlighted in selected:
        selected.discard(highlighted)
    else:
        selected.add(highlighted)
    return replace(state, selected=frozenset(selected))


def _select_visible(state: PickerState) -> PickerState:
    return replace(state, selected=state.selected | set(visible(state)))


def apply_key(state: PickerState, key: str) -> PickerState:
    """Advance the state by one decoded keystroke. Unknown/ignored keys
    return the state unchanged (the driver simply repaints)."""

    if key == KEY_ENTER:
        return replace(state, outcome=OUTCOME_ACCEPT)
    if key == KEY_ESC:
        return replace(state, outcome=OUTCOME_CANCEL)
    if key == KEY_UP:
        return _move(state, -1)
    if key == KEY_DOWN:
        return _move(state, +1)
    if key == KEY_BACKSPACE:
        return _with_query(state, state.query[:-1])
    if key == KEY_TAB:
        return _toggle(state)
    if key == KEY_CTRL_A:
        return _select_visible(state)
    if len(key) == 1 and key.isprintable():
        return _with_query(state, state.query + key)
    return state


def resolved_selection(state: PickerState) -> tuple[str, ...]:
    """The flows to compare once the user accepts: the toggled set (in flow
    order) if any are toggled, otherwise the single highlighted flow, or
    nothing when the filtered list is empty (spec 'Enter semantics')."""

    if state.selected:
        return tuple(flow for flow in state.flows if flow in state.selected)
    rows = visible(state)
    if not rows:
        return ()
    return (rows[state.cursor],)


def _style(text: str, *, color: bool, code: str) -> str:
    return f"{code}{text}{_RESET}" if color else text


def render(state: PickerState, *, color: bool = False) -> str:
    """The stderr view: a header, the live filter line, then one row per
    visible flow with a cursor marker and a checkbox. Emits NO ANSI escapes
    when `color=False` (unit tests force it off)."""

    lines = [_style(_HEADER, color=color, code=_BOLD), f"> {state.query}"]
    rows = visible(state)
    if not rows:
        lines.append("  (no matching flows)")
    for index, flow in enumerate(rows):
        checkbox = "[x]" if flow in state.selected else "[ ]"
        is_cursor = index == state.cursor
        pointer = "▶" if is_cursor else " "
        row = f"{pointer} {checkbox} {flow}"
        lines.append(_style(row, color=color, code=_REVERSE) if is_cursor else row)
    return "\n".join(lines)
