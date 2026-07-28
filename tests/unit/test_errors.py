"""Unit tests for `cli/output/errors.py` — the shared CLI error renderer,
salient-line extraction, and hint mapping. Pure functions, no I/O."""

from __future__ import annotations

from perf.cli.output.errors import (
    hint_for_diagnostics,
    render_error,
    salient_tool_line,
)

_ANSI = "\x1b["

# A realistic Flashlight-on-adb-failure blob: a one-line real cause buried
# in ~15 lines of Node/JS stack frames and a raw byte-Buffer dump.
_NOISY_FLASHLIGHT_STDERR = """node:internal/errors:856
  const err = new Error(message);
              ^

Error: Command failed: adb shell getprop ro.build.version.sdk
adb: device unauthorized.
This adb server's $ADB_VENDOR_KEYS is not set
Try 'adb kill-server' if that seems wrong.

    at checkExecSyncError (node:child_process:820:11)
    at Object.execSync (node:child_process:891:15)
    at execSync (pkg/prelude/bootstrap.js:2111:30)
  status: 1,
  signal: null,
  output: [
    null,
    Buffer(0) [Uint8Array] [],
"""


# ===== render_error =====


def test_render_error_plain_is_a_drop_in_for_the_old_echo():
    assert render_error("boom", color=False) == "Error: boom"


def test_render_error_colors_only_the_prefix_when_color_on():
    out = render_error("boom", color=True)
    assert _ANSI in out
    assert out.endswith(" boom")  # message body itself stays uncolored


def test_render_error_backtick_spans_bold_when_color_on_and_strip_when_off():
    plain = render_error("pass `--force` to overwrite", color=False)
    assert plain == "Error: pass --force to overwrite"  # backticks removed, no literal `
    colored = render_error("pass `--force` to overwrite", color=True)
    assert "`" not in colored  # never leak a literal backtick
    assert _ANSI in colored


def test_render_error_appends_cause_and_hint_lines():
    out = render_error("flow failed", color=False, cause="adb: device unauthorized", hint="do X")
    assert out.splitlines() == [
        "Error: flow failed",
        "  cause: adb: device unauthorized",
        "  hint: do X",
    ]


def test_render_error_omits_absent_cause_and_hint():
    assert "\n" not in render_error("flow failed", color=False)


# ===== salient_tool_line =====


def test_salient_line_prefers_the_adb_line_out_of_a_node_stack_dump():
    assert salient_tool_line(_NOISY_FLASHLIGHT_STDERR) == "adb: device unauthorized."


def test_salient_line_skips_node_frames_and_buffer_dump():
    line = salient_tool_line(_NOISY_FLASHLIGHT_STDERR)
    assert "node:internal" not in (line or "")
    assert "Buffer" not in (line or "")
    assert "at " not in (line or "")


def test_salient_line_none_for_empty_or_missing():
    assert salient_tool_line(None) is None
    assert salient_tool_line("   \n  \n") is None


def test_salient_line_falls_back_to_first_meaningful_line_without_adb():
    assert salient_tool_line("boom happened\nmore detail") == "boom happened"


def test_salient_line_finds_enoent_tail_over_generic_command_failed_error():
    """Regression: a Flashlight `writeReport` crash on a missing `results/`
    dir surfaces the real cause on a line further down than the generic
    `Error: Command failed: ...` line (which is deliberately excluded by the
    `Error:` tier) — the ENOENT signal must still be picked, not line one."""
    diagnostics = (
        "Running on Pixel_8_Pro\n"
        "Error: Command failed with exit code 1: flashlight test\n"
        "    at ChildProcess.exithandler (node:child_process:400:5)\n"
        "Caused by: ENOENT: no such file or directory, open "
        "'results/prestamos-warm-warm-1699999999.json'\n"
    )
    line = salient_tool_line(diagnostics)
    assert line is not None
    assert "ENOENT" in line
    assert line != "Running on Pixel_8_Pro"


def test_salient_line_finds_maestro_assertion_failed():
    diagnostics = (
        "Running on Pixel_8_Pro\n"
        "Maestro test run failed\n"
        'Assertion failed: element "Login button" not found\n'
    )
    line = salient_tool_line(diagnostics)
    assert line == 'Assertion failed: element "Login button" not found'


def test_salient_line_finds_iteration_failed_exited_with_code():
    diagnostics = "Running on Pixel_8_Pro\n🚨 Iteration 1/1 failed: maestro exited with code 1\n"
    line = salient_tool_line(diagnostics)
    assert line == "🚨 Iteration 1/1 failed: maestro exited with code 1"
    assert line != "Running on Pixel_8_Pro"


# ===== hint_for_diagnostics =====


def test_hint_maps_device_unauthorized():
    hint = hint_for_diagnostics(_NOISY_FLASHLIGHT_STDERR)
    assert hint is not None
    assert "adb kill-server" in hint


def test_hint_maps_multiple_devices():
    hint = hint_for_diagnostics("adb: more than one device/emulator")
    assert hint is not None
    assert "--device" in hint


def test_hint_maps_no_device_across_adb_variants():
    # Every realistic "no usable device" adb wording maps to the same hint —
    # including `device '<serial>' not found`, where the serial sits between
    # "device" and "not found".
    for diag in (
        "adb: no devices/emulators found",
        "error: no devices/emulators found",
        "adb: device offline",
        "error: device 'emulator-5554' not found",
    ):
        hint = hint_for_diagnostics(diag)
        assert hint is not None, diag
        assert "adb devices" in hint


def test_hint_none_when_signature_unrecognized():
    assert hint_for_diagnostics("some unrelated failure") is None
    assert hint_for_diagnostics(None) is None


def test_hint_maps_missing_binary_style_enoent_to_missing_tool():
    """A Python spawn `FileNotFoundError` on a missing binary reads
    `No such file or directory: '<name>'` — the quoted target has NO `/`
    path separator. That IS a missing-tool-from-PATH situation."""
    hint = hint_for_diagnostics("No such file or directory: 'flashlight'")
    assert hint is not None
    assert "missing from PATH" in hint


def test_hint_does_not_map_generic_path_enoent_to_missing_tool():
    """Regression: a file/dir ENOENT (e.g. a missing `results/` output path)
    is NOT a missing-tool situation — the quoted target has a `/` path
    separator. Emitting the "missing tool" hint here is actively misleading;
    no hint (None) is better than a wrong one."""
    diagnostics = "ENOENT: no such file or directory, open 'results/foo.json'"
    hint = hint_for_diagnostics(diagnostics)
    assert hint is None or "missing from PATH" not in hint
