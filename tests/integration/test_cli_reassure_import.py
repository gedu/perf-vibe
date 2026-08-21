"""End-to-end CLI harness for `perfvibe reassure-import <path>` — REAL
`typer` app + REAL `SqliteStore` (a real temp SQLite file) + REAL
`ReassureJsonlParser`, driven through `typer.testing.CliRunner` (SKILL/
`python-testing` rule 3: "every code path must be exercised through the
REAL wiring at least once" — only `load_config` is faked, mirroring
`test_cli_markers.py`/`test_cli_history.py`, to avoid touching the real
`~/.config/perf/config.toml` on the test machine).

Exit-code discipline (reassure-ingest spec "Exit-Code Discipline"): `2`
missing/unreadable path, `0` success/duplicate/zero-entries, `3` a
store/transaction failure. `1` MUST NEVER appear anywhere in this suite.
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

import perf.cli.commands.reassure_import as reassure_import_module
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


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_bytes(content.encode("utf-8"))
    return path


# ===== exit 2: missing/unreadable path =====


def test_missing_path_exits_2_with_no_json_payload(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    missing = tmp_path / "does-not-exist.perf"

    result = runner.invoke(main_module.app, ["--json", "reassure-import", str(missing)])

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""  # no --json payload emitted at all


def test_missing_path_exits_2_pretty(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    missing = tmp_path / "does-not-exist.perf"

    result = runner.invoke(main_module.app, ["reassure-import", str(missing)])

    assert result.exit_code == 2, result.output
    assert "Error:" in result.output


# ===== exit 0: successful import + idempotent re-import =====


def test_successful_import_then_reimport_is_already_imported(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    first = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])
    assert first.exit_code == 0, first.output
    payload = json.loads(first.stdout)
    assert payload["schema_version"] == 2
    assert payload["already_imported"] is False
    assert payload["entries_imported"] == 4
    assert payload["duration_samples_imported"] > 0
    assert payload["count_samples_imported"] > 0
    assert payload["entries_skipped"] == 6

    second = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])
    assert second.exit_code == 0, second.output
    payload2 = json.loads(second.stdout)
    assert payload2["already_imported"] is True
    assert payload2["entries_imported"] == 0
    assert payload2["duration_samples_imported"] == 0
    assert payload2["count_samples_imported"] == 0


def test_entries_with_render_issues_counted_end_to_end_from_the_real_fixture(
    monkeypatch, tmp_path: Path
):
    """The committed fixture carries exactly ONE entry with
    `issues.initialUpdateCount` greater than zero (the WidgetPanel entry, at
    `1`); the other three are `0`, `0`, and `issues` absent entirely. So the
    payload must report `1` — proving the counter counts NON-ZERO entries, not
    entries that merely HAVE an `issues` block (which would be 3) and not all
    imported entries (4). A duplicate re-import reports `0`, like every other
    `*_imported` counter, because nothing was imported."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    first = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])
    assert first.exit_code == 0, first.output
    payload = json.loads(first.stdout)
    assert payload["entries_imported"] == 4
    assert payload["entries_with_render_issues"] == 1

    second = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["entries_with_render_issues"] == 0


def test_entries_with_render_issues_is_zero_when_no_entry_is_flagged(monkeypatch, tmp_path: Path):
    """A file whose entries all report `initialUpdateCount: 0` (or omit
    `issues`) imports cleanly and reports `0` — the counter never confuses
    "flagged nothing" with "imported nothing"."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    clean = _write(
        tmp_path,
        "clean.perf",
        '{"name": "a", "runs": 1, "durations": [1.0], "counts": [1.0], '
        '"issues": {"initialUpdateCount": 0, "redundantUpdates": []}}\n'
        '{"name": "b", "runs": 1, "durations": [2.0], "counts": [2.0]}\n',
    )

    result = runner.invoke(main_module.app, ["--json", "reassure-import", str(clean)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["entries_imported"] == 2
    assert payload["entries_with_render_issues"] == 0


def test_duration_and_count_counters_are_independently_reported(monkeypatch, tmp_path: Path):
    """The committed fixture's non-alignment entry (8 counts / 6 durations)
    proves the two counters are never forced equal end-to-end through the
    CLI (design "Two series counters")."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["duration_samples_imported"] != payload["count_samples_imported"]


# ===== exit 0: zero entries recovered from a readable file =====


def test_all_lines_malformed_exits_0_with_zero_entries_and_a_warning(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    bad_file = _write(tmp_path, "all-bad.perf", "not valid json at all\nalso not json")

    result = runner.invoke(main_module.app, ["--json", "reassure-import", str(bad_file)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["entries_imported"] == 0
    assert payload["already_imported"] is False
    assert "warning" in result.stderr


def test_mixed_quality_file_warns_per_skipped_line_and_stdout_is_json_pure(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])

    assert result.exit_code == 0, result.output
    # stdout is EXACTLY the JSON payload — one line, parses cleanly.
    payload = json.loads(result.stdout)
    assert payload["entries_skipped"] == 6
    # Every warning landed on stderr, never stdout.
    assert result.stderr.count("warning:") == 6
    assert "warning:" not in result.stdout


def test_pretty_mode_reports_kind_and_counts(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["reassure-import", str(_FIXTURE)])

    assert result.exit_code == 0, result.output
    assert "entries_imported" in result.output


# ===== exit 3: store/transaction failure =====


def test_store_failure_exits_3(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    class _BoomStore:
        def save_reassure_import(self, *args, **kwargs):
            raise RuntimeError("simulated store failure")

        def close(self) -> None:
            pass

    monkeypatch.setattr(reassure_import_module, "build_store", lambda *a, **kw: _BoomStore())

    result = runner.invoke(main_module.app, ["--json", "reassure-import", str(_FIXTURE)])

    assert result.exit_code == 3, result.output
    assert result.stdout.strip() == ""


# ===== exit 1 must never appear =====


def test_exit_1_never_appears_anywhere_in_this_suite(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    missing = tmp_path / "does-not-exist.perf"
    bad_file = _write(tmp_path, "all-bad.perf", "not valid json at all")

    for args in (
        ["--json", "reassure-import", str(missing)],
        ["--json", "reassure-import", str(_FIXTURE)],
        ["--json", "reassure-import", str(bad_file)],
    ):
        result = runner.invoke(main_module.app, args)
        assert result.exit_code != 1, (args, result.output)


# ===== invalid `--kind` is a usage error, not a store failure =====


def test_invalid_kind_override_exits_2_not_3(monkeypatch, tmp_path: Path):
    """An invalid `--kind` fails the store's own adapter-boundary
    validation (`ValueError`), which the CLI maps to exit `2` (a usage
    error caused by the CLI flag) — never exit `3` (which is reserved for
    a genuine store/transaction failure)."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(
        main_module.app, ["--json", "reassure-import", str(_FIXTURE), "--kind", "nightly"]
    )

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""


# ===== `--kind` override + config fallback =====


def test_kind_override_flows_through_to_the_payload(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(
        main_module.app, ["--json", "reassure-import", str(_FIXTURE), "--kind", "baseline"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["kind"] == "baseline"


def test_kind_derived_from_basename_when_not_overridden(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    current_copy = tmp_path / "current.perf"
    current_copy.write_bytes(_FIXTURE.read_bytes())

    result = runner.invoke(main_module.app, ["--json", "reassure-import", str(current_copy)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["kind"] == "current"


def test_missing_path_argument_falls_back_to_config_reassure_path(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    configured = tmp_path / "configured.perf"
    configured.write_bytes(_FIXTURE.read_bytes())
    _patch_load_config(monkeypatch, db_path=str(db_path), reassure_path=str(configured))

    result = runner.invoke(main_module.app, ["--json", "reassure-import"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["path"] == str(configured)
