"""End-to-end CLI harness for `perfvibe reassure entries <import-id>` —
REAL `typer` app + REAL `SqliteStore` (a real temp SQLite file) + REAL
registry (python-testing rule 3: "every code path must be exercised
through the REAL wiring at least once" — the store is NEVER monkeypatched
here, matching `test_cli_reassure_list.py`). Only `load_config` is faked,
to avoid touching the real `~/.config/perf/config.toml`.

Seeds the store through `perfvibe reassure import` itself (the real
persistence path already covered end-to-end by
`test_cli_reassure_import.py`), rather than reaching around it.

Requirement: spec "reassure entries <import-id> — One Import's Entries" —
`name`/`entry_type`/declared `runs`/each series' independent `n`; an
unknown `import-id` MUST exit `2` with no `--json` payload emitted.

The trap this suite exists to catch (see `contracts/reassure_entries_v1.py`
and `domain/ports.py`'s `Store.reassure_import_exists` docstrings):
`Store.reassure_entries(import_id)` returns `()` for BOTH an unknown id
AND a real import with zero entries, so emptiness alone cannot pick the
right exit code. Both states are exercised here, explicitly, with
different expected exit codes.
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

from perf.config.loader import PerfConfig

main_module = import_module("perf.cli.main")

runner = CliRunner()


def _patch_load_config(monkeypatch, **overrides) -> PerfConfig:
    defaults: dict = {"no_color": True}
    defaults.update(overrides)
    config = PerfConfig(**defaults)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    return config


def _write_entry_line(
    *, name: str, entry_type: str = "render", runs: int, durations: list, counts: list
) -> str:
    return json.dumps(
        {"name": name, "type": entry_type, "runs": runs, "durations": durations, "counts": counts}
    )


def _write_perf_file(tmp_path: Path, name: str, *entry_lines: str) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(entry_lines) + "\n", encoding="utf-8")
    return path


def _write_all_malformed_perf_file(tmp_path: Path, name: str) -> Path:
    """A readable `.perf` file with zero recoverable entries — every line
    fails to parse, so `import` still creates an import row
    (`already_imported: false`) but `entries_imported == 0` (mirrors
    `test_cli_reassure_import.py::test_all_lines_malformed_exits_0_with_zero_entries_and_a_warning`).
    """
    path = tmp_path / name
    path.write_text("not valid json at all\nalso not json\n", encoding="utf-8")
    return path


def _import(path: Path) -> int:
    result = runner.invoke(main_module.app, ["--json", "reassure", "import", str(path)])
    assert result.exit_code == 0, result.output
    # `already_imported: true` (re-import of a byte-identical file) never
    # returns a fresh `import_id` — callers must always pass a NEW file.
    imports = runner.invoke(main_module.app, ["--json", "reassure", "list"])
    return json.loads(imports.stdout)["imports"][0]["import_id"]


# ===== valid import id: every entry, independent n's (5-count/8-duration) =====


def test_valid_import_id_returns_every_entry_with_independent_ns(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    path = _write_perf_file(
        tmp_path,
        "a.perf",
        _write_entry_line(
            name="WidgetPanel renders correctly",
            runs=8,
            durations=[10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8],
            counts=[1.0, 1.0, 1.0, 1.0, 1.0],
        ),
    )
    import_id = _import(path)

    result = runner.invoke(main_module.app, ["--json", "reassure", "entries", str(import_id)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["import_id"] == import_id
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["name"] == "WidgetPanel renders correctly"
    assert entry["entry_type"] == "render"
    assert entry["runs"] == 8
    assert entry["duration"]["n"] == 8
    assert entry["count"]["n"] == 5
    assert entry["duration"]["unit"] == "ms"
    assert entry["count"]["unit"] == "count"


def test_multiple_entries_all_returned_in_store_order(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    path = _write_perf_file(
        tmp_path,
        "a.perf",
        _write_entry_line(name="First", runs=1, durations=[1.0], counts=[1.0]),
        _write_entry_line(name="Second", runs=1, durations=[2.0], counts=[2.0]),
    )
    import_id = _import(path)

    result = runner.invoke(main_module.app, ["--json", "reassure", "entries", str(import_id)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [entry["name"] for entry in payload["entries"]] == ["First", "Second"]


# ===== unknown import id: usage error, exit 2, no --json payload =====


def test_unknown_import_id_exits_2_with_no_json_payload(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "entries", "999999"])

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""


def test_unknown_import_id_exits_2_pretty(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["reassure", "entries", "999999"])

    assert result.exit_code == 2, result.output
    assert "Error" in result.output


# ===== real import, zero entries: exit 0, empty list — NOT the unknown-id error =====


def test_real_import_with_zero_entries_exits_0_with_empty_list(monkeypatch, tmp_path: Path):
    """The disambiguation this whole suite exists for: a real import whose
    file recovered zero entries is NOT the same as an unknown import id —
    it must exit `0` with `entries: []`, never `2`."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    path = _write_all_malformed_perf_file(tmp_path, "all-bad.perf")
    import_id = _import(path)

    result = runner.invoke(main_module.app, ["--json", "reassure", "entries", str(import_id)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["import_id"] == import_id
    assert payload["entries"] == []


# ===== pretty mode reaches the renderer, `--json` stays byte-pure =====


def test_pretty_mode_reaches_the_renderer(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    path = _write_perf_file(
        tmp_path, "a.perf", _write_entry_line(name="First", runs=1, durations=[1.0], counts=[1.0])
    )
    import_id = _import(path)

    result = runner.invoke(main_module.app, ["reassure", "entries", str(import_id)])

    assert result.exit_code == 0, result.output
    assert "perfvibe reassure entries" in result.output
    assert "entries" not in result.output.split("\n")[0]  # header line, not a leaked JSON key


def test_json_stdout_is_exactly_one_parseable_payload(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    path = _write_perf_file(
        tmp_path, "a.perf", _write_entry_line(name="First", runs=1, durations=[1.0], counts=[1.0])
    )
    import_id = _import(path)

    result = runner.invoke(main_module.app, ["--json", "reassure", "entries", str(import_id)])

    assert result.exit_code == 0, result.output
    json.loads(result.stdout)  # raises if stdout carries anything but the payload


# ===== exit 3: store failure =====


def test_store_failure_exits_3(monkeypatch, tmp_path: Path):
    """Mirrors `test_cli_reassure_list.py::test_store_failure_exits_3`: an
    unexpected store failure is a runtime/tooling error (exit `3`), never
    exit `1` (SKILL rule 7)."""
    import perf.cli.commands.reassure as reassure_module

    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    class _BoomStore:
        def reassure_import_exists(self, *args, **kwargs):
            raise RuntimeError("simulated store failure")

        def close(self) -> None:
            pass

    monkeypatch.setattr(reassure_module, "build_store", lambda *a, **kw: _BoomStore())

    result = runner.invoke(main_module.app, ["--json", "reassure", "entries", "1"])

    assert result.exit_code == 3, result.output
    assert result.stdout.strip() == ""


def test_exit_1_never_appears_anywhere_in_this_suite(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "entries", "999999"])

    assert result.exit_code != 1
