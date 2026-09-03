"""End-to-end CLI harness for `perfvibe reassure run` (D6, reassure-read
PR5, the LAST slice of this capability) — REAL `typer` app + REAL
`SqliteStore` (a real temp SQLite file), driven through
`typer.testing.CliRunner`. Mirrors `test_cli_reassure_import.py`'s "only
`load_config` is faked" discipline, except for the two scenarios that must
exercise the REAL `config/loader.py` validation (an invalid
`reassure_command`) or the REAL `SubprocessRunner.run_streamed` boundary
(the noisy-stdout trap) — those two isolate `GLOBAL_CONFIG_PATH` instead
of faking `load_config` wholesale.

**THE TRAP THIS FILE EXISTS TO PIN**: `reassure run` is the ONLY command
in this whole capability that spawns an external process. `npx reassure`
prints progress/warnings/jest output; if ANY of that lands on perfvibe's
own stdout while `--json` is active, the payload stops being parseable —
every automated consumer breaks at once. `test_noisy_subprocess_stdout_
still_parses_as_exactly_one_json_object` is the load-bearing test below,
not an incidental one — it spawns a REAL (`sys.executable`) subprocess
that is genuinely noisy: several lines, something JSON-shaped, and a line
containing a bare brace, exactly so a tidy single-line fake could never
have passed it by accident.

Exit-code discipline (spec "Exit-Code Discipline", NEVER `1`): `0`
success — reusing `reassure_import_v1` VERBATIM (A11, no new contract);
`2` usage error (an invalid `reassure_command`); `3` the subprocess itself
exited non-zero — and in that case NO import is ever attempted.
"""

from __future__ import annotations

import json
import sys
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

from perf.adapters.process import CommandResult
from perf.adapters.process import SubprocessRunner as RealSubprocessRunner
from perf.config import loader as config_loader_module
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


def _fake_run_streamed(*, returncode: int, stdout: str = "", stderr: str = "", lines=()):
    def fake(self, argv, *, env=None, cwd=None, on_line=None):
        for line in lines:
            if on_line is not None:
                on_line(line)
        return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)

    return fake


# ===== exit 0: `reassure run` reuses the SAME parse-then-store path,
# producing the SAME payload shape/exit code as `reassure import <path>` =====


def test_run_and_import_produce_the_same_payload_shape_and_exit_code(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(RealSubprocessRunner, "run_streamed", _fake_run_streamed(returncode=0))

    run_db = tmp_path / "run.db"
    _patch_load_config(monkeypatch, db_path=str(run_db), reassure_path=str(_FIXTURE))
    run_result = runner.invoke(main_module.app, ["--json", "reassure", "run"])
    assert run_result.exit_code == 0, run_result.output
    run_payload = json.loads(run_result.stdout)

    import_db = tmp_path / "import.db"
    _patch_load_config(monkeypatch, db_path=str(import_db))
    import_result = runner.invoke(main_module.app, ["--json", "reassure", "import", str(_FIXTURE)])
    assert import_result.exit_code == 0, import_result.output
    import_payload = json.loads(import_result.stdout)

    # Same shape (key set), same exit code, same values — reassure_import_v1
    # reused verbatim (A11): `run` performs literally the same operation,
    # just sourcing `path` from config instead of a CLI argument.
    assert run_payload.keys() == import_payload.keys()
    assert run_payload == import_payload
    assert run_payload["schema_version"] == 3
    assert run_payload["already_imported"] is False
    assert run_payload["entries_imported"] == 4


# ===== exit 3: a failing subprocess skips the import entirely =====


def test_failing_subprocess_exits_3_and_no_import_is_attempted(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        RealSubprocessRunner,
        "run_streamed",
        _fake_run_streamed(returncode=1, stderr="Error: reassure command failed"),
    )

    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path), reassure_path=str(_FIXTURE))

    result = runner.invoke(main_module.app, ["--json", "reassure", "run"])

    assert result.exit_code == 3, result.output
    assert result.stdout.strip() == ""  # no --json payload on a subprocess failure

    # Prove no import landed: `reassure list` reports zero imports.
    list_result = runner.invoke(main_module.app, ["--json", "reassure", "list"])
    assert list_result.exit_code == 0, list_result.output
    assert json.loads(list_result.stdout)["imports"] == []


def test_failing_subprocess_pretty_mode_shows_error_never_exit_1(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        RealSubprocessRunner, "run_streamed", _fake_run_streamed(returncode=1, stderr="boom")
    )
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path), reassure_path=str(_FIXTURE))

    result = runner.invoke(main_module.app, ["reassure", "run"])

    assert result.exit_code == 3, result.output
    assert result.exit_code != 1
    assert "Error:" in result.output


# ===== THE load-bearing test: a genuinely noisy REAL subprocess must never
# leak onto stdout under --json (the trap this whole slice exists to avoid) =====


_NOISY_SCRIPT = (
    "print('Running reassure...')\n"
    'print(\'{"progress": "50%", "stage": "warmup"}\')\n'
    "print('} totally not valid json by itself {')\n"
    "print('WARN: something noisy happened')\n"
    "print('Done.')\n"
)


def test_noisy_subprocess_stdout_still_parses_as_exactly_one_json_object(
    monkeypatch, tmp_path: Path
):
    """Genuinely noisy: multiple lines, something JSON-shaped, and a line
    containing a bare brace — a tidy one-line fake would prove nothing
    here. Uses the REAL `SubprocessRunner.run_streamed` (no monkeypatch on
    it) so this is a true end-to-end proof of the stdout/stderr split."""

    db_path = tmp_path / "perf.db"
    _patch_load_config(
        monkeypatch,
        db_path=str(db_path),
        reassure_path=str(_FIXTURE),
        reassure_command=(sys.executable, "-c", _NOISY_SCRIPT),
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "run"])

    assert result.exit_code == 0, result.output
    # The WHOLE stdout must parse as exactly one JSON object — any leaked
    # noise (a prefix, a suffix, interleaving) breaks `json.loads` outright.
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 3
    assert payload["entries_imported"] == 4

    # Every noisy line landed on stderr instead, verbatim.
    assert "Running reassure..." in result.stderr
    assert "WARN: something noisy happened" in result.stderr
    assert "Done." in result.stderr


# ===== exit 2: an invalid `reassure_command` is a usage error — exercises
# the REAL `config/loader.py` validation, not a faked `load_config` =====


def test_invalid_reassure_command_string_in_toml_exits_2(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        config_loader_module, "GLOBAL_CONFIG_PATH", tmp_path / "nonexistent-global.toml"
    )
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text('reassure_command = "yarn reassure"\n')

    result = runner.invoke(
        main_module.app, ["--json", "--config", str(config_path), "reassure", "run"]
    )

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""
    assert "reassure_command" in result.stderr


def test_empty_reassure_command_array_in_toml_exits_2(monkeypatch, tmp_path: Path):
    """An explicit empty array is just as invalid as a bare string — there
    is nothing to execute."""
    monkeypatch.setattr(
        config_loader_module, "GLOBAL_CONFIG_PATH", tmp_path / "nonexistent-global.toml"
    )
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text("reassure_command = []\n")

    result = runner.invoke(
        main_module.app, ["--json", "--config", str(config_path), "reassure", "run"]
    )

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""


# ===== exit 1 must never appear =====


def test_exit_1_never_appears_anywhere_in_this_suite(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        RealSubprocessRunner, "run_streamed", _fake_run_streamed(returncode=1, stderr="boom")
    )
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path), reassure_path=str(_FIXTURE))

    result = runner.invoke(main_module.app, ["--json", "reassure", "run"])
    assert result.exit_code != 1, result.output
