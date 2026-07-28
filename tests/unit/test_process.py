"""Unit tests for `SubprocessRunner.run_streamed()` (`run-live-progress`
design "Relay mechanism" / threat matrix "Subprocess spawn" + "Secret in
streamed output").

RED-before-GREEN: written before `run_streamed` existed on
`src/perf/adapters/process.py`. Spawns REAL, tiny, deterministic child
processes via `sys.executable -c "..."` — not a live device/adb/maestro
binary — to prove the streaming/relay/scrub behavior of this module
directly (SKILL rule 8 is about never touching a real device/tool, not
about avoiding a real, local, throwaway Python child process).
"""

from __future__ import annotations

import inspect
import re
import sys
import time

from perf.adapters.process import CommandResult, SubprocessRunner, bounded_diagnostics

_PASSWORD = "s3cr3t-value"


def _run(*, script: str, env: dict[str, str] | None = None) -> tuple[CommandResult, list[str]]:
    runner = SubprocessRunner()
    relayed: list[str] = []
    result = runner.run_streamed(
        [sys.executable, "-c", script],
        env=env,
        on_line=relayed.append,
    )
    return result, relayed


def test_relays_each_line_live_in_order():
    script = "print('one'); print('two'); print('three')"
    result, relayed = _run(script=script)

    assert relayed == ["one", "two", "three"]
    assert result.returncode == 0
    assert isinstance(result, CommandResult)


def test_returns_same_command_result_shape_as_run():
    script = "print('hello')"
    result, _ = _run(script=script)

    assert hasattr(result, "returncode")
    assert hasattr(result, "stdout")
    assert hasattr(result, "stderr")
    assert "hello" in result.stdout
    assert "hello" in result.stderr  # merged stream in BOTH fields


def test_non_zero_exit_still_returns_meaningful_diagnostics():
    script = "import sys; print('device offline', file=sys.stderr); sys.exit(1)"
    result, relayed = _run(script=script)

    assert result.returncode == 1
    assert "device offline" in result.stderr
    assert "device offline" in relayed


def test_argv_is_always_a_list_never_a_shell_string():
    """Source-level guard (SKILL rule 5), mirroring the existing
    `test_real_subprocess_runner_never_uses_shell_true` guard on `.run()`."""
    from perf.adapters import process as process_module

    source = inspect.getsource(process_module)
    assert "shell=True" not in source
    assert "shell = True" not in source


def test_empty_output_is_handled_without_crashing():
    script = "pass"
    result, relayed = _run(script=script)

    assert result.returncode == 0
    assert relayed == []
    assert result.stdout == ""
    assert result.stderr == ""


def test_final_line_without_trailing_newline_is_not_dropped():
    script = "import sys; sys.stdout.write('no-newline-tail')"
    result, relayed = _run(script=script)

    assert relayed == ["no-newline-tail"]
    assert "no-newline-tail" in result.stdout


def test_embedded_carriage_return_does_not_crash_or_drop_content():
    script = "import sys; sys.stdout.write('progress: 50%\\rprogress: 100%\\n')"
    result, relayed = _run(script=script)

    # Universal-newlines translation treats a bare `\r` as a line terminator
    # too — the important guarantee under test is: no crash, and neither
    # half of the text is silently lost.
    joined = "\n".join(relayed)
    assert "progress: 50%" in joined
    assert "progress: 100%" in joined
    assert result.returncode == 0


def test_streams_a_large_multi_line_output_without_hanging():
    """Corner case: reading a single merged pipe (stdout+stderr) must never
    deadlock, even under a large volume of interleaved output — proves the
    chosen `stderr=subprocess.STDOUT` merge (design decision) in practice,
    not just by inspection."""
    line_count = 5000
    script = (
        "import sys\n"
        f"for i in range({line_count}):\n"
        "    print(f'stdout-{i}')\n"
        "    print(f'stderr-{i}', file=sys.stderr)\n"
    )
    started = time.monotonic()
    result, relayed = _run(script=script)
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    assert len(relayed) == line_count * 2
    assert elapsed < 10, f"run_streamed took {elapsed:.1f}s — looks hung/deadlocked"


def test_on_line_is_optional_accumulator_still_populated():
    """`on_line` is opt-in — a caller that only wants the final
    `CommandResult` (no live relay) must still get the accumulated text."""
    runner = SubprocessRunner()
    result = runner.run_streamed([sys.executable, "-c", "print('one'); print('two')"])

    assert result.returncode == 0
    assert "one" in result.stdout
    assert "two" in result.stdout


def test_accumulator_is_defensively_bounded_for_a_very_chatty_child():
    """`run_streamed`'s OWN accumulator cap: every line still reaches
    `on_line` live regardless, but the returned `CommandResult` text never
    grows unbounded even for a genuinely huge stream (well past the 50,000
    char internal cap)."""
    line_count = 2000
    line_body = "x" * 100  # 2000 * 100 chars = 200,000 raw chars, way over the cap
    script = f"line = '{line_body}'\nfor _ in range({line_count}): print(line)"
    result, relayed = _run(script=script)
    raw_total_chars = line_count * len(line_body)

    assert len(relayed) == line_count  # every line still relayed live, uncapped
    assert len(result.stdout) < raw_total_chars  # accumulator capped, not unbounded


def test_per_line_secret_scrubbing_before_relay_and_in_accumulator():
    """RED leak test (design threat matrix "Secret in streamed output"): a
    `PASSWORD` forwarded via `--env` in argv must be scrubbed from EVERY
    relayed line AND from the accumulated `CommandResult` text — never just
    one or the other."""
    runner = SubprocessRunner()
    relayed: list[str] = []
    script = f"print('auth failed for user with PASSWORD={_PASSWORD}')"
    argv = [sys.executable, "-c", script, "--env", f"PASSWORD={_PASSWORD}"]

    result = runner.run_streamed(argv, on_line=relayed.append)

    assert relayed, "expected at least one relayed line"
    for line in relayed:
        assert _PASSWORD not in line
    assert _PASSWORD not in result.stdout
    assert _PASSWORD not in result.stderr
    assert "***" in "".join(relayed)


# ===== bounded_diagnostics =====


def test_bounded_diagnostics_returns_none_for_empty_or_blank():
    assert bounded_diagnostics("") is None
    assert bounded_diagnostics("   \n  \n") is None


def test_bounded_diagnostics_passes_short_text_through_unchanged():
    assert bounded_diagnostics("device offline") == "device offline"


def test_bounded_diagnostics_keeps_head_and_tail_not_just_head():
    """Regression: the real failure line (e.g. an `ENOENT` from Flashlight's
    `writeReport`, or a Maestro assertion) is very often at the TAIL of a
    verbose tool's output — a head-only trim silently dropped it. Both the
    head AND the tail must survive bounding, with a truncation marker
    identifying how much was omitted in between."""
    head_marker = "HEAD-START-MARKER"
    tail_marker = "TAIL-END-MARKER"
    filler = "x" * 5000
    text = f"{head_marker}\n{filler}\n{tail_marker}"

    result = bounded_diagnostics(text)

    assert result is not None
    assert result.startswith(head_marker)
    assert result.endswith(tail_marker)
    assert "truncated" in result
    assert len(result) < len(text)


def test_bounded_diagnostics_honors_a_small_max_len():
    """Regression: `max_len` must be honored for ANY caller, not just the
    module's default (where head+tail happens to equal 2000). A small
    `max_len` must still yield a result no longer than `max_len` (marker
    included), with an accurate, non-negative "truncated N chars" count —
    previously the head/tail sizes were fixed module constants that ignored
    the caller-supplied `max_len` entirely."""
    stripped_text = "H" * 5 + "x" * 300 + "T" * 5
    max_len = 40

    result = bounded_diagnostics(stripped_text, max_len=max_len)

    assert result is not None
    assert len(result) <= max_len, f"result length {len(result)} exceeds max_len {max_len}"

    match = re.search(r"truncated (\d+) chars\) \.\.\.", result)
    assert match is not None, "expected a truncation marker with a chars count"
    omitted = int(match.group(1))
    assert omitted >= 0

    marker_start = result.index("\n... (truncated")
    marker_end = result.index("...\n") + len("...\n")
    head_survived = result[:marker_start]
    tail_survived = result[marker_end:]
    assert stripped_text.startswith(head_survived)
    assert stripped_text.endswith(tail_survived)
    assert omitted == len(stripped_text) - len(head_survived) - len(tail_survived)
