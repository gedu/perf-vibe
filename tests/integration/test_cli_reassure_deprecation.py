"""End-to-end CLI harness proving the D1/A10 deprecated flat alias — REAL
`typer` app + REAL `SqliteStore` (a real temp SQLite file), driven through
`typer.testing.CliRunner` (python-testing rule 3: real wiring for the
command dispatch/deprecation echo, matching `test_cli_reassure_import.py`).

`perfvibe reassure-import <path>` keeps working, hidden from `--help`, and
echoes Click's native invocation-time deprecation notice (design A10,
`typer/_click/core.py:738-743`) to stderr only — while `perfvibe reassure
import <path>` (the sub-app form, registering the SAME function object)
stays completely silent (spec "Flat alias still works, hidden from
`--help`")."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

from perf.config.loader import PerfConfig

main_module = import_module("perf.cli.main")

runner = CliRunner()

_FIXTURE = Path("tests/fixtures/reassure_sample.perf")


def _patch_load_config(monkeypatch, **overrides) -> PerfConfig:
    defaults: dict = {"no_color": True}
    defaults.update(overrides)
    config = PerfConfig(**defaults)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    return config


def test_flat_and_subapp_forms_produce_byte_identical_json_and_exit_code(monkeypatch, tmp_path):
    db_path = tmp_path / "flat.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    flat = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])
    assert flat.exit_code == 0, flat.output

    db_path2 = tmp_path / "sub.db"
    _patch_load_config(monkeypatch, db_path=str(db_path2))
    sub = runner.invoke(main_module.app, ["--json", "reassure", "import", str(_FIXTURE)])
    assert sub.exit_code == 0, sub.output

    assert flat.exit_code == sub.exit_code
    flat_payload = json.loads(flat.stdout)
    sub_payload = json.loads(sub.stdout)
    assert flat_payload == sub_payload


def test_help_omits_the_flat_reassure_import_command(monkeypatch, tmp_path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "reassure-import" not in result.output


def test_subapp_help_lists_import_and_list(monkeypatch, tmp_path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["reassure", "--help"])

    assert result.exit_code == 0, result.output
    assert "import" in result.output
    assert "list" in result.output


def test_flat_form_deprecation_notice_lands_on_stderr_only_stdout_stays_json_pure(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])

    assert result.exit_code == 0, result.output
    json.loads(result.stdout)  # stdout parses cleanly — no notice text leaked in
    assert "deprecated" in result.stderr.lower()
    assert "reassure-import" in result.stderr
    assert "use `perfvibe reassure import` instead" in result.stderr


def test_subapp_import_form_prints_no_deprecation_notice(monkeypatch, tmp_path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "import", str(_FIXTURE)])

    assert result.exit_code == 0, result.output
    assert "deprecated" not in result.stderr.lower()
