"""Config problems are USAGE errors end-to-end: a malformed or ill-typed
`perfvibe.toml`, or an explicitly named `--config` file that does not exist,
must exit `2` with one clean `Error:` block on stderr — never a raw
traceback, and NEVER exit `1` (exit 1 is `budget-check`'s gate signal that
CI trusts; `run`/`compare` must never emit it, SKILL rule 7).

Previously `load_config` ran unguarded inside the typer callback, so a
broken config escaped as a `TOMLDecodeError`/`ValueError` traceback with
exit 1 — for EVERY command, before any subcommand's exit-code discipline
even started. These tests drive the REAL app with the REAL loader (no
`load_config` monkeypatching — the loader IS the code under test).
"""

from __future__ import annotations

from importlib import import_module

import pytest
from typer.testing import CliRunner

from perf.config import loader

main_module = import_module("perf.cli.main")

runner = CliRunner()

COMMANDS = (
    ["run", "checkout"],
    ["compare", "checkout"],
    ["budget-check", "checkout"],
)


@pytest.fixture(autouse=True)
def _isolated_cwd(monkeypatch, tmp_path):
    # Never read the developer's real global config, and resolve project
    # discovery against the temp dir the test writes its broken config into.
    monkeypatch.setattr(loader, "GLOBAL_CONFIG_PATH", tmp_path / "nonexistent-global.toml")
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda argv: argv[0])
def test_malformed_toml_exits_2_with_clean_error(tmp_path, argv):
    (tmp_path / "perfvibe.toml").write_text('driver = "maestro\n')  # unterminated string

    result = runner.invoke(main_module.app, argv)

    assert result.exit_code == 2, result.output
    assert "Error:" in result.stderr
    assert "perfvibe.toml" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda argv: argv[0])
def test_ill_typed_config_value_exits_2_with_clean_error(tmp_path, argv):
    (tmp_path / "perfvibe.toml").write_text('default_iterations = "ten"\n')

    result = runner.invoke(main_module.app, argv)

    assert result.exit_code == 2, result.output
    assert "Error:" in result.stderr
    assert "default_iterations" in result.stderr
    assert "Traceback" not in result.stderr


def test_explicit_config_path_that_does_not_exist_exits_2_naming_the_path(tmp_path):
    result = runner.invoke(main_module.app, ["--config", "typo.toml", "compare", "checkout"])

    assert result.exit_code == 2, result.output
    # The REAL problem (the path) is named — not a downstream "unknown flow".
    assert "Error:" in result.stderr
    assert "typo.toml" in result.stderr
    assert "unknown flow" not in result.stderr
