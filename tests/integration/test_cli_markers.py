"""End-to-end CLI harness for `perfvibe markers snippet`/`markers doctor` —
REAL `typer` app (`add_typer` sub-app, the first nested-Typer command group
in the repo) driven through `typer.testing.CliRunner`. Mirrors
`test_cli_init.py`'s discipline: `load_config` is faked (it would otherwise
read the real `~/.config/perf/config.toml`), but every command body runs
for real — including the REAL `AdbLogcatMarkerSource().parse()` seam
`doctor` reuses.

**Design's #1 risk** (markers-command design.md "Testing Strategy"):
proving a GLOBAL flag (`--json`, parsed by `main_callback` BEFORE Typer
resolves the `markers doctor` sub-command) actually reaches the sub-app's
`ctx.obj` through `add_typer` — untested, this is exactly the kind of
plumbing that silently breaks.

TTY simulation mirrors `test_cli_init_wizard.py`'s documented technique:
`CliRunner.invoke()` wires `sys.stdin` to a non-TTY stream by default, so
patching the CLASS `typer.testing._NamedTextIOWrapper.isatty` is required
to simulate an interactive terminal (`markers doctor`'s "line" mode with no
piped stdin).
"""

from __future__ import annotations

import json
from importlib import import_module

import typer.testing as typer_testing
from typer.testing import CliRunner

from perf.config.loader import PerfConfig

main_module = import_module("perf.cli.main")

runner = CliRunner()


def _patch_load_config(monkeypatch, **overrides) -> PerfConfig:
    """Mirrors `test_cli_init.py`'s helper — no markers command reads
    `perf_config` for its own logic; faking `load_config` only avoids
    touching the real `~/.config/perf/config.toml` on the test machine."""

    defaults: dict = {"no_color": True}
    defaults.update(overrides)
    config = PerfConfig(**defaults)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    return config


def _simulate_tty(monkeypatch) -> None:
    """Patches the CLASS `typer.testing._NamedTextIOWrapper.isatty` (see
    `test_cli_init_wizard.py`'s module docstring for why the class, not an
    instance) so `sys.stdin.isatty()` reads `True` inside the invoked
    command — `markers doctor`'s "argument given, nothing piped" happy
    path."""

    monkeypatch.setattr(typer_testing._NamedTextIOWrapper, "isatty", lambda self: True)


def _raise_os_error_on_read(self) -> str:
    raise OSError("simulated stdin read failure")


# ===== markers snippet =====


def test_snippet_ts_pretty_is_paste_ready_code_only(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "snippet"])
    assert result.exit_code == 0, result.output
    assert "markStart" in result.stdout
    assert "MARKERS" in result.stdout
    assert ": string" in result.stdout  # default lang is ts


def test_snippet_js_pretty_has_no_ts_type_annotations(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "snippet", "--lang", "js"])
    assert result.exit_code == 0, result.output
    assert "markStart" in result.stdout
    assert ": string" not in result.stdout


def test_snippet_json_shape_is_exactly_schema_version_lang_code(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(main_module.app, ["--json", "markers", "snippet", "--lang", "js"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload.keys()) == {"schema_version", "lang", "code"}
    assert payload["lang"] == "js"
    assert "markStart" in payload["code"]


def test_snippet_pretty_on_a_tty_stdout_skips_the_non_tty_nudge(monkeypatch):
    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "snippet"])
    assert result.exit_code == 0, result.output
    assert "note: non-terminal output detected" not in result.output


def test_snippet_unknown_lang_is_a_usage_error(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "snippet", "--lang", "python"])
    assert result.exit_code == 2, result.output
    assert result.stdout == ""


# ===== markers doctor: ctx.obj propagation (design's #1 risk) =====


def test_json_flag_before_subcommand_reaches_doctor_via_ctx_obj(monkeypatch):
    """`--json` is parsed by `main_callback` BEFORE `markers doctor` is
    even resolved (add_typer nesting) — this proves `ctx.obj` (set once at
    the root) actually reaches the sub-app's command body."""

    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    result = runner.invoke(main_module.app, ["--json", "markers", "doctor", "[PERF] x: 12ms"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["mode"] == "line"
    assert payload["breakdown"]["parsed"] == [{"name": "x", "value": 12.0, "unit": "ms"}]


# ===== markers doctor: single-line mode =====


def test_doctor_single_line_that_parses_reports_the_marker(monkeypatch):
    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "doctor", "[PERF] cold_start: 812ms"])
    assert result.exit_code == 0, result.output
    assert "cold_start" in result.stdout
    assert "coverage_ok: True" in result.stdout


def test_doctor_single_line_that_fails_reports_the_specific_reason_and_still_exits_0(monkeypatch):
    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "doctor", "[PERF] not-a-number: abcms"])
    assert result.exit_code == 0, result.output
    assert "malformed_text" in result.stdout
    assert "coverage_ok: False" in result.stdout


# ===== markers doctor: stdin mode =====


def test_doctor_stdin_mode_breakdown_of_a_mixed_capture(monkeypatch):
    _patch_load_config(monkeypatch)
    oversized = "[PERF] checkout: " + ("9" * 5000) + "ms"
    buffer = "\n".join(
        [
            "[PERF] checkout: 900ms",
            "[PERF] markStart:onboarding",
            '[PERF-META] {"app_version":"4.20.1"}',
            "[PERF] not-a-number: abcms",
            oversized,
            "unrelated log line",
        ]
    )
    result = runner.invoke(main_module.app, ["--json", "markers", "doctor"], input=buffer)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["mode"] == "stdin"
    breakdown = payload["breakdown"]
    assert breakdown["parsed"] == [{"name": "checkout", "value": 900.0, "unit": "ms"}]
    assert breakdown["mark_start_without_end"] == 1
    assert breakdown["perf_meta"] == 1
    assert breakdown["ignored"] == 1
    failures = {entry["reason"]: entry["line"] for entry in breakdown["parse_failures"]}
    assert failures["malformed_text"] == "[PERF] not-a-number: abcms"
    assert failures["oversized"] == oversized[:120] + "…"
    assert payload["coverage_ok"] is True


def test_doctor_stdin_mode_with_zero_perf_lines_is_still_exit_0(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(
        main_module.app, ["--json", "markers", "doctor"], input="just some app logs\n"
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["breakdown"]["parsed"] == []
    assert payload["coverage_ok"] is False


# ===== markers doctor: usage errors (never exit 1) =====


def test_doctor_both_arg_and_piped_stdin_is_a_usage_error(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(
        main_module.app, ["markers", "doctor", "[PERF] x: 1ms"], input="also piped\n"
    )
    assert result.exit_code == 2, result.output
    assert result.exit_code != 1


def test_doctor_neither_arg_nor_piped_stdin_is_a_usage_error(monkeypatch):
    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "doctor"])
    assert result.exit_code == 2, result.output
    assert result.exit_code != 1


def test_doctor_unknown_flag_is_a_usage_error(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "doctor", "--bogus-flag"])
    assert result.exit_code == 2, result.output
    assert result.exit_code != 1


# ===== markers doctor: runtime failure =====


def test_doctor_stdin_read_failure_exits_3_never_1(monkeypatch):
    _patch_load_config(monkeypatch)
    monkeypatch.setattr(
        typer_testing._NamedTextIOWrapper, "read", _raise_os_error_on_read, raising=False
    )
    result = runner.invoke(main_module.app, ["markers", "doctor"], input="anything\n")
    assert result.exit_code == 3, result.output
    assert result.exit_code != 1


# ===== markers: group-level --help/-h (W-2 fix) =====


def test_markers_group_help_lists_snippet_and_doctor(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "--help"])
    assert result.exit_code == 0, result.output
    assert "snippet" in result.output
    assert "doctor" in result.output


def test_markers_group_short_help_flag_also_works(monkeypatch):
    _patch_load_config(monkeypatch)
    result = runner.invoke(main_module.app, ["markers", "-h"])
    assert result.exit_code == 0, result.output
    assert "snippet" in result.output
    assert "doctor" in result.output
