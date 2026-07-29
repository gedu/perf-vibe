"""Unit tests for the PURE fzf-style flow-picker state machine
(`cli/output/flow_picker.py`) — no terminal I/O at all (SKILL rule 8:
split pure logic from side effects; drive the logic directly). Exercises
incremental filtering, cursor clamping as the visible list shrinks, Tab
multi-select toggling, Ctrl-A selecting only the visible subset, and the
Enter/Esc terminal semantics.
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
    OUTCOME_ACCEPT,
    OUTCOME_CANCEL,
    apply_key,
    initial_state,
    render,
    resolved_selection,
    visible,
)

FLOWS = ("checkout", "login", "search", "settings")


def _feed(state, keys):
    for key in keys:
        state = apply_key(state, key)
    return state


def test_initial_state_shows_every_flow_no_selection():
    state = initial_state(FLOWS)
    assert visible(state) == FLOWS
    assert state.cursor == 0
    assert state.selected == frozenset()
    assert state.outcome is None


def test_typing_filters_case_insensitively_and_shrinks_the_list():
    state = _feed(initial_state(FLOWS), "SE")
    assert visible(state) == ("search", "settings")


def test_backspace_edits_the_filter_and_regrows_the_list():
    state = _feed(initial_state(FLOWS), "se")
    state = apply_key(state, KEY_BACKSPACE)
    assert state.query == "s"
    assert visible(state) == ("search", "settings")


def test_cursor_clamps_when_the_filtered_list_shrinks_under_it():
    state = initial_state(FLOWS)
    state = _feed(state, [KEY_DOWN, KEY_DOWN, KEY_DOWN])  # cursor -> 3 (settings)
    assert state.cursor == 3
    state = apply_key(state, "l")  # only "login" matches -> list of 1
    assert visible(state) == ("login",)
    assert state.cursor == 0


def test_cursor_moves_and_clamps_at_both_ends():
    state = initial_state(FLOWS)
    state = apply_key(state, KEY_UP)  # already at top, stays
    assert state.cursor == 0
    state = _feed(state, [KEY_DOWN, KEY_DOWN, KEY_DOWN, KEY_DOWN, KEY_DOWN])
    assert state.cursor == len(FLOWS) - 1  # clamped at bottom


def test_tab_toggles_multi_select_on_the_highlighted_flow():
    state = initial_state(FLOWS)
    state = apply_key(state, KEY_TAB)  # toggle checkout on
    assert state.selected == frozenset({"checkout"})
    state = apply_key(state, KEY_TAB)  # toggle checkout off
    assert state.selected == frozenset()


def test_ctrl_a_selects_only_the_currently_visible_flows():
    state = _feed(initial_state(FLOWS), "se")  # visible: search, settings
    state = apply_key(state, KEY_CTRL_A)
    assert state.selected == frozenset({"search", "settings"})
    # A flow filtered OUT was never selected.
    assert "checkout" not in state.selected


def test_enter_with_toggles_resolves_to_the_selected_set_in_flow_order():
    state = initial_state(FLOWS)
    state = apply_key(state, KEY_TAB)  # checkout
    state = _feed(state, [KEY_DOWN, KEY_DOWN, KEY_TAB])  # + search
    state = apply_key(state, KEY_ENTER)
    assert state.outcome == OUTCOME_ACCEPT
    assert resolved_selection(state) == ("checkout", "search")


def test_enter_with_no_toggles_resolves_to_the_single_highlighted_flow():
    state = initial_state(FLOWS)
    state = _feed(state, [KEY_DOWN])  # highlight "login"
    state = apply_key(state, KEY_ENTER)
    assert state.outcome == OUTCOME_ACCEPT
    assert resolved_selection(state) == ("login",)


def test_enter_on_empty_filtered_list_resolves_to_nothing():
    state = _feed(initial_state(FLOWS), "zzz")  # nothing matches
    assert visible(state) == ()
    state = apply_key(state, KEY_ENTER)
    assert resolved_selection(state) == ()


def test_esc_marks_cancel_outcome():
    state = apply_key(initial_state(FLOWS), KEY_ESC)
    assert state.outcome == OUTCOME_CANCEL


def test_toggle_survives_filtering_it_out_and_back():
    """A toggled flow stays selected even while filtered off-screen, and the
    final resolution still includes it (selection is by NAME, not by visible
    row index)."""
    state = initial_state(FLOWS)
    state = apply_key(state, KEY_TAB)  # checkout selected
    state = _feed(state, "log")  # filter to login only; checkout off-screen
    assert visible(state) == ("login",)
    assert state.selected == frozenset({"checkout"})
    state = apply_key(state, KEY_ENTER)
    assert resolved_selection(state) == ("checkout",)


def test_render_marks_cursor_and_checkboxes_and_shows_query():
    state = initial_state(FLOWS)
    state = apply_key(state, KEY_TAB)  # select checkout
    state = _feed(state, [KEY_DOWN])  # cursor on login
    out = render(state, color=False)
    assert "checkout" in out
    assert "login" in out
    # Selected flow shows a filled checkbox, unselected an empty one.
    assert "[x] checkout" in out
    assert "[ ] login" in out


def test_render_color_off_has_no_ansi_escapes():
    out = render(initial_state(FLOWS), color=False)
    assert "\x1b[" not in out
