"""Unit tests for the concrete `ProgressReporter` (`domain/ports.py`)
implementations in `cli/output/progress.py` (`run-live-progress` design:
"a concrete StderrProgressReporter owns all rendering on STDERR").

Append-only sequential model (correctness fix, Slice B): TTY and non-TTY
render IDENTICALLY except for optional color — no cursor-control byte is
ever emitted, in either mode. `relayed_line()` interleaved between
`iteration_started`/`iteration_finished` was the exact bug this replaces:
an in-place redraw table tracked how many rows it had painted, but
`relayed_line()` printed a line WITHOUT updating that count, so the next
redraw's cursor-up count went stale and clobbered the wrong terminal rows
on every driver-managed TTY run that relayed step output.

Emoji vocabulary LOCKED to exactly ⏳ (pending) / ✅ (ok) / ❌ (failed) — spec
'Locked Emoji Status Vocabulary'.
"""

from __future__ import annotations

import io

from perf.cli.output.progress import (
    NullProgressReporter,
    StderrProgressReporter,
    build_progress_reporter,
)


def _make(*, stderr_is_tty: bool, error_color_enabled: bool = False) -> tuple:
    stream = io.StringIO()
    reporter = StderrProgressReporter(
        stream=stream,
        stderr_is_tty=stderr_is_tty,
        error_color_enabled=error_color_enabled,
    )
    return reporter, stream


def _assert_no_cursor_control_bytes(output: str) -> None:
    # Cursor-up (`\x1b[<n>A`) and clear-line (`\x1b[2K`) bytes must NEVER
    # appear — only a color escape (`\x1b[1;3xm` / `\x1b[0m`) is allowed,
    # and only when color is enabled.
    assert "\x1b[2K" not in output
    for n in range(1, 100):
        assert f"\x1b[{n}A" not in output


def test_build_progress_reporter_returns_a_concrete_reporter():
    """The CLI-layer composition-root factory returns a CONCRETE
    `ProgressReporter` — every Protocol method callable without error.
    (Moved out of the adapter registry: building a concrete reporter is a
    presentation concern, so the factory lives here, not in
    `adapters/registry.py`.)"""
    reporter = build_progress_reporter()

    reporter.iteration_started(1, 3)
    reporter.iteration_finished(1, 3, ok=True)
    reporter.awaiting_user_input("Perform the flow manually, then press Enter.")
    reporter.relayed_line("some relayed tool output")


def test_null_progress_reporter_emits_nothing():
    reporter = NullProgressReporter()
    # Every Protocol method must be callable and produce no observable
    # effect — there is no stream to assert against by construction.
    reporter.iteration_started(1, 3)
    reporter.iteration_finished(1, 3, ok=True)
    reporter.awaiting_user_input("prompt")
    reporter.relayed_line("some tool output")


def test_relayed_line_between_iteration_events_appends_in_order_with_no_cursor_bytes():
    """REGRESSION test for the exact production bug: `relayed_line` calls
    interleaved between `iteration_started`/`iteration_finished` must never
    desync any redraw bookkeeping — because there IS no redraw bookkeeping
    left. Exercised for BOTH `stderr_is_tty=True` and `False`; neither may
    ever emit a cursor-control byte."""
    for stderr_is_tty in (True, False):
        reporter, stream = _make(stderr_is_tty=stderr_is_tty)

        reporter.iteration_started(1, 2)
        reporter.relayed_line("step a")
        reporter.relayed_line("step b")
        reporter.iteration_finished(1, 2, ok=True)

        output = stream.getvalue()
        lines = output.splitlines()
        assert lines == [
            "⏳ iteration 1/2",
            "   step a",
            "   step b",
            "✅ iteration 1/2",
        ]
        _assert_no_cursor_control_bytes(output)


def test_non_tty_emits_plain_sequential_lines_with_zero_cursor_control_bytes():
    reporter, stream = _make(stderr_is_tty=False)

    reporter.iteration_started(1, 2)
    reporter.relayed_line("maestro step 1")
    reporter.iteration_finished(1, 2, ok=True)
    reporter.iteration_started(2, 2)
    reporter.iteration_finished(2, 2, ok=False)

    output = stream.getvalue()
    _assert_no_cursor_control_bytes(output)
    assert "⏳" in output
    assert "✅" in output
    assert "❌" in output
    assert "maestro step 1" in output


def test_tty_and_non_tty_render_identically_apart_from_color():
    """Append-only model: TTY and non-TTY must produce the SAME text (with
    color off) — the rendering split is gone, only color gating remains."""
    tty_reporter, tty_stream = _make(stderr_is_tty=True)
    non_tty_reporter, non_tty_stream = _make(stderr_is_tty=False)

    for reporter in (tty_reporter, non_tty_reporter):
        reporter.iteration_started(1, 1)
        reporter.relayed_line("only step")
        reporter.iteration_finished(1, 1, ok=True)

    assert tty_stream.getvalue() == non_tty_stream.getvalue()
    _assert_no_cursor_control_bytes(tty_stream.getvalue())


def test_iteration_finished_ok_golden_with_color_forced_off():
    reporter, stream = _make(stderr_is_tty=False, error_color_enabled=False)
    reporter.iteration_finished(1, 2, ok=True)
    assert stream.getvalue() == "✅ iteration 1/2\n"


def test_iteration_finished_failed_golden_with_color_forced_off():
    reporter, stream = _make(stderr_is_tty=False, error_color_enabled=False)
    reporter.iteration_finished(2, 2, ok=False)
    assert stream.getvalue() == "❌ iteration 2/2\n"


def test_iteration_finished_ok_golden_with_color_enabled_is_bold_green():
    reporter, stream = _make(stderr_is_tty=False, error_color_enabled=True)
    reporter.iteration_finished(1, 2, ok=True)
    assert stream.getvalue() == "\x1b[1;32m✅ iteration 1/2\x1b[0m\n"


def test_iteration_finished_failed_golden_with_color_enabled_is_bold_red():
    reporter, stream = _make(stderr_is_tty=False, error_color_enabled=True)
    reporter.iteration_finished(2, 2, ok=False)
    assert stream.getvalue() == "\x1b[1;31m❌ iteration 2/2\x1b[0m\n"


def test_awaiting_user_input_writes_the_prompt_to_the_injected_stream():
    reporter, stream = _make(stderr_is_tty=False)
    reporter.awaiting_user_input("[1/3] Perform the flow manually, then confirm.")
    assert "Perform the flow manually" in stream.getvalue()


def test_relayed_line_writes_indented_to_the_injected_stream():
    reporter, stream = _make(stderr_is_tty=False)
    reporter.relayed_line("RUN Onboarding Flow")
    assert stream.getvalue() == "   RUN Onboarding Flow\n"


def test_default_stream_is_sys_stderr(monkeypatch, capsys):
    reporter = StderrProgressReporter(stderr_is_tty=False)
    reporter.relayed_line("hello from stderr")
    captured = capsys.readouterr()
    assert captured.err.strip() == "hello from stderr"
    assert captured.out == ""
