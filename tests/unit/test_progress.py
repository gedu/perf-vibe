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

from perf.application.run_flow import RunFlowResult
from perf.cli.output.progress import (
    NullProgressReporter,
    StderrProgressReporter,
    build_progress_reporter,
)


def _result(**overrides) -> RunFlowResult:
    defaults: dict = {
        "run_id": 1,
        "flow_name": "checkout",
        "device_key": "Pixel-Fake|14|physical",
        "git_commit": "abc123",
        "is_dev_bundle": None,
        "source": "local:test",
        "mode": "warm",
        "iterations": 2,
        "markers": (),
        "samples": (),
        "raw_report_path": None,
        "partial_coverage": False,
        "iteration_statuses": None,
    }
    defaults.update(overrides)
    return RunFlowResult(**defaults)


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


# ===== run-live-progress Slice C: recap() =====


def test_recap_renders_true_per_iteration_table_when_iteration_statuses_available():
    """C.3 (RED): with `iteration_statuses` populated (Flashlight sampler),
    `recap()` renders a TRUE per-iteration ✅/❌ row per entry — including a
    row for a failed/excluded iteration (partial coverage), never
    fabricating an all-✅ table."""
    reporter, stream = _make(stderr_is_tty=False)
    result = _result(
        iterations=3,
        partial_coverage=True,
        iteration_statuses=[True, False, True],
    )

    reporter.recap(result)

    lines = stream.getvalue().splitlines()
    assert lines == [
        "Recap · checkout · 3 iteration(s)",
        "✅ iteration 1/3",
        "❌ iteration 2/3",
        "✅ iteration 3/3",
    ]


def test_recap_honest_coverage_summary_when_no_iteration_statuses_and_partial():
    """When the active sampler/marker-source never surfaced per-iteration
    status (`iteration_statuses is None`), `recap()` must NOT fabricate a
    per-iteration ✅ — it renders an honest coarse summary instead, using
    `partial_coverage` for the partial case. FIX 3 (correctness/cleanup):
    the coarse summary uses ONLY the locked ⏳/✅/❌ vocabulary + plain
    words — never a 4th glyph (the ⚠️ this branch used to emit)."""
    reporter, stream = _make(stderr_is_tty=False)
    result = _result(iterations=4, partial_coverage=True, iteration_statuses=None)

    reporter.recap(result)

    output = stream.getvalue()
    assert "Recap · checkout · 4 iteration(s)" in output
    assert "❌" in output
    assert "partial coverage" in output
    assert "⚠️" not in output
    # Never a fabricated per-iteration ✅/❌ row when there is no real
    # per-iteration data to draw one from.
    assert "iteration 1/4" not in output


def test_recap_honest_coverage_summary_when_no_iteration_statuses_and_complete():
    reporter, stream = _make(stderr_is_tty=False)
    result = _result(iterations=2, partial_coverage=False, iteration_statuses=None)

    reporter.recap(result)

    output = stream.getvalue()
    assert "Recap · checkout · 2 iteration(s)" in output
    assert "✅" in output
    assert "complete" in output
    assert "⚠️" not in output


def test_recap_empty_iteration_statuses_falls_back_to_honest_coverage_line():
    """FIX 1 (correctness): `FlashlightSampler.parse()` returns
    `iteration_statuses=[]` (an EMPTY list, NOT `None`) when the results
    JSON has zero `iterations[]` entries. An empty list must be treated the
    SAME as `None` — falling through to the honest coarse summary — never
    silently skipping the per-iteration loop and rendering NO coverage
    line at all (just the header, in silence)."""
    reporter, stream = _make(stderr_is_tty=False)
    result = _result(iterations=3, partial_coverage=True, iteration_statuses=[])

    reporter.recap(result)

    lines = stream.getvalue().splitlines()
    assert lines[0] == "Recap · checkout · 3 iteration(s)"
    # A real, honest coverage line MUST follow the header — never silence.
    assert len(lines) >= 2
    assert any("partial coverage" in line for line in lines[1:])
    assert not any(
        line.startswith("✅ iteration") or line.startswith("❌ iteration") for line in lines
    )


def test_recap_reports_requested_vs_reported_when_counts_disagree():
    """FIX 2 (correctness): the header must never contradict the rendered
    per-iteration rows. When the REQUESTED count (`result.iterations`)
    disagrees with the ACTUAL reported count (`len(iteration_statuses)`),
    the header surfaces BOTH numbers honestly instead of picking the
    requested count and rendering rows that visibly disagree with it."""
    reporter, stream = _make(stderr_is_tty=False)
    result = _result(iterations=5, partial_coverage=True, iteration_statuses=[True, False, True])

    reporter.recap(result)

    lines = stream.getvalue().splitlines()
    assert lines[0] == "Recap · checkout · 5 requested · 3 reported"
    assert lines[1:] == [
        "✅ iteration 1/3",
        "❌ iteration 2/3",
        "✅ iteration 3/3",
    ]


def test_recap_header_and_rows_share_the_same_count_when_they_agree():
    """FIX 2 companion: when requested == reported, the header stays the
    simple `N iteration(s)` form (no "requested/reported" noise) and every
    row denominator matches it."""
    reporter, stream = _make(stderr_is_tty=False)
    result = _result(iterations=3, partial_coverage=True, iteration_statuses=[True, False, True])

    reporter.recap(result)

    lines = stream.getvalue().splitlines()
    assert lines[0] == "Recap · checkout · 3 iteration(s)"
    assert all(line.endswith("/3") for line in lines[1:])


def test_recap_never_emits_pending_glyph():
    """Recap renders ONCE after completion — every iteration is already
    resolved, so the ⏳ (pending) glyph must never appear in a recap."""
    reporter, stream = _make(stderr_is_tty=False)
    result = _result(iterations=2, partial_coverage=False, iteration_statuses=[True, True])

    reporter.recap(result)

    assert "⏳" not in stream.getvalue()


# ===== run-live-progress Slice C fix: run_header() (FIX 4) =====


def test_run_header_writes_a_non_indented_top_level_line():
    """FIX 4 (cleanup): the TOOL_MANAGED framing header used to be emitted
    via `relayed_line` inside `driver_maestro.py` — 3-space indented, so it
    read like a nested relayed tool line. `run_header()` is a dedicated,
    concrete-only method (mirrors `recap()`) that writes a DISTINCT
    top-level line — never indented."""
    reporter, stream = _make(stderr_is_tty=False)

    reporter.run_header("checkout", 2)

    assert stream.getvalue() == "🎯 checkout · 2 iterations via Flashlight\n"


def test_run_header_is_not_on_the_progress_reporter_protocol():
    """`run_header()` lives ONLY on the concrete `StderrProgressReporter` —
    same placement rule as `recap()` — never on the pure `ProgressReporter`
    Protocol (`domain/ports.py`), and `NullProgressReporter` never needs
    one either since `run.py` only calls it on the concrete reporter it
    already retained."""
    assert not hasattr(NullProgressReporter(), "run_header")
