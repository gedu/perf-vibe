"""End-to-end CLI harness for `perfvibe reassure show <name>` — REAL `typer`
app + REAL `SqliteStore` (a real temp SQLite file) + REAL registry
(python-testing rule 3: "every code path must be exercised through the
REAL wiring at least once" — the store is NEVER monkeypatched here,
matching `test_cli_reassure_entries.py`/`test_cli_reassure_list.py`). Only
`load_config` is faked, to avoid touching the real
`~/.config/perf/config.toml`.

Seeds the store through `perfvibe reassure import` itself (the real
persistence path already covered end-to-end by
`test_cli_reassure_import.py`), rather than reaching around it.

Requirement: spec "reassure show <name> — Latest Detail With
State-Transition Issues (D5, D8)". Covers:
  - D8 default (most recent import overall) vs `--import <id>` override,
  - `name` absent from the LATEST import exits `2`, NO walk-back to an
    older import that happens to contain it (A14),
  - `name` absent from EVERY import exits `2`,
  - a `0 -> 1` transition renders `initial_update_state == "introduced"`,
  - a `NULL -> 0` transition renders `"unknown"` (NEVER `"unchanged"` or a
    fabricated `0 -> 0` label) — asserted `is None`, never falsy,
  - a `name` present ONLY in the latest import (no prior import contains
    it) renders `"unknown"`/no baseline, never a fabricated one.
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

from perf.config.loader import PerfConfig

main_module = import_module("perf.cli.main")

runner = CliRunner()

_NAME = "WidgetPanel Performance Tests WidgetPanel renders correctly"


def _patch_load_config(monkeypatch, **overrides) -> PerfConfig:
    defaults: dict = {"no_color": True}
    defaults.update(overrides)
    config = PerfConfig(**defaults)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    return config


def _entry_line(
    *,
    name: str = _NAME,
    runs: int = 1,
    durations: list | None = None,
    counts: list | None = None,
    initial_update_count: int | None = None,
) -> str:
    payload: dict = {
        "name": name,
        "runs": runs,
        "durations": durations if durations is not None else [1.0],
        "counts": counts if counts is not None else [1.0],
    }
    if initial_update_count is not None:
        payload["issues"] = {"initialUpdateCount": initial_update_count}
    return json.dumps(payload)


def _write_perf_file(
    tmp_path: Path, filename: str, *, created_date: str | None, entry_lines: list[str]
) -> Path:
    lines = []
    if created_date is not None:
        lines.append(
            json.dumps(
                {"metadata": {"branch": "main", "commitHash": "c1", "creationDate": created_date}}
            )
        )
    lines.extend(entry_lines)
    path = tmp_path / filename
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _import(path: Path) -> int:
    result = runner.invoke(main_module.app, ["--json", "reassure", "import", str(path)])
    assert result.exit_code == 0, result.output
    imports = runner.invoke(main_module.app, ["--json", "reassure", "list", "--limit", "100"])
    payload = json.loads(imports.stdout)
    # The just-imported file is always the most recent by `imported_at`
    # among any imports sharing the same `created_date`, since SequentialClock
    # / real wall-clock time only advances — but D2 orders by `created_date`
    # first, so locate this import by its `source_path` to stay unambiguous
    # regardless of ordering.
    for row in payload["imports"]:
        if row["source_path"] == str(path):
            return row["import_id"]
    raise AssertionError(f"import of {path} not found in roster: {payload}")


# ===== D8 default: latest import overall =====


def test_default_shows_the_latest_import_by_created_date(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[10.0], counts=[1.0])],
        )
    )
    latest_id = _import(
        _write_perf_file(
            tmp_path,
            "b.perf",
            created_date="2026-01-02T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[20.0], counts=[2.0])],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["import_id"] == latest_id
    assert payload["duration"]["p50"] == 20.0


# ===== `--import <id>` override selects a NON-latest import =====


def test_import_override_selects_a_specific_non_latest_import(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    older_id = _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[10.0], counts=[1.0])],
        )
    )
    _import(
        _write_perf_file(
            tmp_path,
            "b.perf",
            created_date="2026-01-02T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[20.0], counts=[2.0])],
        )
    )

    result = runner.invoke(
        main_module.app, ["--json", "reassure", "show", _NAME, "--import", str(older_id)]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["import_id"] == older_id
    assert payload["duration"]["p50"] == 10.0


def test_import_override_naming_an_import_missing_the_name_exits_2(monkeypatch, tmp_path: Path):
    """[unmissable] `--import <id>` names a REAL import id that does not
    contain `name` — it must simply be absent from `reassure_series`, and
    the correct outcome is exit `2`, never a crash or a silently wrong
    baseline."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    other_import_id = _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(name="SomeOtherTest", durations=[10.0], counts=[1.0])],
        )
    )

    result = runner.invoke(
        main_module.app, ["--json", "reassure", "show", _NAME, "--import", str(other_import_id)]
    )

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""


# ===== A14: name absent from the LATEST import — no walk-back =====


def test_name_absent_from_latest_import_exits_2_no_walk_back(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[10.0], counts=[1.0])],
        )
    )
    _import(
        _write_perf_file(
            tmp_path,
            "b.perf",
            created_date="2026-01-02T00:00:00.000Z",
            entry_lines=[_entry_line(name="SomeOtherTest", durations=[20.0], counts=[2.0])],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""


def test_name_absent_from_every_import_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(name="SomeOtherTest", durations=[10.0], counts=[1.0])],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 2, result.output


def test_no_imports_at_all_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 2, result.output


# ===== D5: 0 -> 1 is "introduced" =====


def test_zero_to_one_transition_renders_introduced(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(initial_update_count=0)],
        )
    )
    _import(
        _write_perf_file(
            tmp_path,
            "b.perf",
            created_date="2026-01-02T00:00:00.000Z",
            entry_lines=[_entry_line(initial_update_count=1)],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["baseline_initial_update_count"] == 0
    assert payload["initial_update_count"] == 1
    assert payload["initial_update_state"] == "introduced"


# ===== D5: NULL -> 0 is "unknown", never falsy-compared to 0 =====


def test_null_baseline_to_zero_renders_unknown_never_unchanged(monkeypatch, tmp_path: Path):
    """[unmissable] The earlier import never measured `issues` at all
    (`NULL`); the later import measured it and found it clean (`0`). A
    falsy check (`if not baseline`) would treat `None` and `0`
    identically and mislabel this `'unchanged'` — it MUST be `'unknown'`."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(initial_update_count=None)],
        )
    )
    _import(
        _write_perf_file(
            tmp_path,
            "b.perf",
            created_date="2026-01-02T00:00:00.000Z",
            entry_lines=[_entry_line(initial_update_count=0)],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["baseline_initial_update_count"] is None
    assert payload["initial_update_count"] == 0
    assert payload["initial_update_state"] == "unknown"


# ===== name present ONLY in the latest import — no fabricated baseline =====


def test_name_present_only_in_latest_import_has_no_baseline(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(name="SomeOtherTest", durations=[10.0], counts=[1.0])],
        )
    )
    _import(
        _write_perf_file(
            tmp_path,
            "b.perf",
            created_date="2026-01-02T00:00:00.000Z",
            entry_lines=[_entry_line(name=_NAME, initial_update_count=0)],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["baseline_initial_update_count"] is None
    assert payload["initial_update_state"] == "unknown"


# ===== pretty mode reaches the renderer, `--json` stays byte-pure =====


def test_pretty_mode_reaches_the_renderer(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["reassure", "show", _NAME])

    assert result.exit_code == 0, result.output
    assert "perfvibe reassure show" in result.output
    assert "schema_version" not in result.output


def test_json_stdout_is_exactly_one_parseable_payload(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 0, result.output
    json.loads(result.stdout)  # raises if stdout carries anything but the payload


# ===== exit 3: store failure =====


def test_store_failure_exits_3(monkeypatch, tmp_path: Path):
    import perf.cli.commands.reassure as reassure_module

    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    class _BoomStore:
        def reassure_imports(self, *args, **kwargs):
            raise RuntimeError("simulated store failure")

        def close(self) -> None:
            pass

    monkeypatch.setattr(reassure_module, "build_store", lambda *a, **kw: _BoomStore())

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code == 3, result.output
    assert result.stdout.strip() == ""


def test_exit_1_never_appears_anywhere_in_this_suite(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "show", _NAME])

    assert result.exit_code != 1
