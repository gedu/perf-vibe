"""Interactive wizard path for `perf init` (tasks.md 3.8) — the ONE piece of
`init`'s behavior 3.1-3.7 (PR-B coverage-gap batch) deliberately did NOT
cover: `typer.testing.CliRunner.invoke()` wires `sys.stdin` to a non-TTY
stream by default, so every scenario in `test_cli_init.py` naturally takes
the non-interactive branch (confirmed empirically there). This file forces
the interactive branch by patching `sys.stdin.isatty()` to return `True`.

**How the TTY is simulated**: `typer.testing.CliRunner.isolation()` replaces
`sys.stdin` with a fresh `typer.testing._NamedTextIOWrapper` instance on
EVERY `invoke()` call — patching an instance (or the pre-invoke `sys.stdin`
object) has no effect, since that object is discarded before the command
ever runs. Patching the **class** method (`_NamedTextIOWrapper.isatty`)
works because the replacement instance CliRunner creates during `invoke()`
is still the same (patched) class — confirmed via a throwaway repro before
writing this file. Patching the class also flips `stdout`/`stderr`'s
`isatty()` (same class, same instances CliRunner builds internally), so
`--no-color` is passed explicitly in every test here to keep assertions
text-based rather than fighting ANSI byte noise (byte-for-byte ANSI-off
golden coverage is task 3.10's job, in a separate file).

I13 (TTY, no `--yes`, wizard runs) / I14 (TTY + `--yes`, forced
non-interactive) / I15 (non-TTY, no `--yes`, auto non-interactive — already
covered by `test_cli_init.py`; cross-referenced, not duplicated, in the last
test below).
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import typer.testing as typer_testing
from typer.testing import CliRunner

from perf.config.loader import PerfConfig

main_module = import_module("perf.cli.main")

runner = CliRunner()

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FLOWS_DIR = _FIXTURES_DIR / "flows"
FLOWS_MISMATCH_DIR = _FIXTURES_DIR / "flows_mismatch"


def _patch_load_config(monkeypatch, **overrides) -> PerfConfig:
    """Mirrors `test_cli_init.py`'s helper — `init` never reads `perf_config`
    for its own logic; faking `load_config` only avoids touching the real
    `~/.config/perf/config.toml` on the test machine."""

    defaults: dict = {"no_color": True}
    defaults.update(overrides)
    config = PerfConfig(**defaults)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    return config


def _simulate_tty(monkeypatch) -> None:
    """Patches the CLASS `typer.testing._NamedTextIOWrapper.isatty` (not an
    instance — see module docstring) so `sys.stdin.isatty()` reads `True`
    inside the invoked command, taking `init`'s interactive branch
    (`sys.stdin.isatty() and not yes`)."""

    monkeypatch.setattr(typer_testing._NamedTextIOWrapper, "isatty", lambda self: True)


# ===== I13: TTY, no --yes — wizard prompts shown =====


def test_wizard_prompt_shown_and_blank_enter_accepts_dim_placeholder_default(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    config_path = tmp_path / "perf.toml"

    result = runner.invoke(
        main_module.app,
        ["--no-color", "--json", "--config", str(config_path), "init", str(FLOWS_DIR)],
        input="\n",  # blank Enter — accept the detected default as-is
    )

    assert result.exit_code == 0, result.output
    assert "bundle_id" in result.output  # the wizard prompt itself was shown
    # `--json` output shares stdout with the prompt echo under CliRunner
    # (`visible_input` writes the prompt straight to `sys.stdout`); the
    # payload is still valid JSON on its own line — find it explicitly.
    json_line = next(line for line in result.stdout.splitlines() if line.startswith("{"))
    payload = json.loads(json_line)
    assert payload["bundle_id"] == "com.example.app"  # the single concrete detected value
    assert payload["bundle_id_source"] == "prompt"
    assert config_path.is_file()


def test_wizard_typed_input_overrides_detected_default(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    config_path = tmp_path / "perf.toml"

    result = runner.invoke(
        main_module.app,
        ["--no-color", "--json", "--config", str(config_path), "init", str(FLOWS_DIR)],
        input="com.overridden.app\n",
    )

    assert result.exit_code == 0, result.output
    json_line = next(line for line in result.stdout.splitlines() if line.startswith("{"))
    payload = json.loads(json_line)
    assert payload["bundle_id"] == "com.overridden.app"
    assert payload["bundle_id_source"] == "prompt"


def test_wizard_mismatch_prompt_shown_and_resolves_via_typed_input(monkeypatch, tmp_path):
    """I6: an appId mismatch under an interactive TTY prompts to choose,
    rather than exiting 2 (the non-interactive-without---bundle-id path
    already covered by `test_cli_init.py`)."""

    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    config_path = tmp_path / "perf.toml"

    result = runner.invoke(
        main_module.app,
        ["--no-color", "--json", "--config", str(config_path), "init", str(FLOWS_MISMATCH_DIR)],
        input="com.example.app\n",
    )

    assert result.exit_code == 0, result.output
    assert "conflicting appid" in result.output.lower()
    json_line = next(line for line in result.stdout.splitlines() if line.startswith("{"))
    payload = json.loads(json_line)
    assert payload["bundle_id"] == "com.example.app"
    assert payload["bundle_id_source"] == "prompt"


# ===== I14: TTY + --yes — forced non-interactive despite the simulated TTY =====


def test_yes_forces_non_interactive_despite_simulated_tty(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    _simulate_tty(monkeypatch)
    config_path = tmp_path / "perf.toml"

    result = runner.invoke(
        main_module.app,
        ["--no-color", "--json", "--config", str(config_path), "init", str(FLOWS_DIR), "--yes"],
        # No stdin input provided at all — if `--yes` did NOT force
        # non-interactive, `typer.prompt` would raise `typer.Abort` on EOF
        # and `init` would exit 3 (see `init.py`'s `except typer.Abort`).
    )

    assert result.exit_code == 0, result.output
    assert "bundle_id [" not in result.output  # no wizard prompt text at all
    payload = json.loads(result.stdout)
    assert payload["bundle_id"] == "com.example.app"
    assert payload["bundle_id_source"] == "detected"


# ===== I15: non-TTY, no --yes — auto-detects non-interactive =====


def test_non_tty_without_yes_auto_detects_non_interactive(monkeypatch, tmp_path):
    """Cross-reference, not a duplicate: `test_cli_init.py`'s entire suite
    (3.1-3.7) already exercises this exact branch on every scenario — under
    a REAL (non-simulated) `CliRunner.invoke()`, `sys.stdin.isatty()` is
    `False` and no `isatty` patch is applied there, so `init` already takes
    the non-interactive path throughout that file. This test only pins the
    assumption explicitly, in the file that owns TTY-branch coverage, without
    re-asserting `test_cli_init.py`'s full scenario matrix again here."""

    _patch_load_config(monkeypatch)
    # Deliberately NOT calling `_simulate_tty` — the default CliRunner stdin.
    config_path = tmp_path / "perf.toml"

    result = runner.invoke(
        main_module.app,
        ["--no-color", "--json", "--config", str(config_path), "init", str(FLOWS_DIR)],
        # No input at all: if this were mistakenly interactive, the missing
        # stdin data would raise EOFError -> typer.Abort -> exit 3.
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["bundle_id_source"] == "detected"
