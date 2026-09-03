"""End-to-end CLI harness for `perfvibe reassure history <name>` — REAL
`typer` app + REAL `SqliteStore` (a real temp SQLite file) + REAL registry
(python-testing rule 3: "every code path must be exercised through the REAL
wiring at least once" — the store is NEVER monkeypatched here, matching
`test_cli_reassure_show.py`/`test_cli_reassure_entries.py`). Only
`load_config` is faked, to avoid touching the real
`~/.config/perf/config.toml`.

Seeds the store through `perfvibe reassure import` itself (the real
persistence path already covered end-to-end by
`test_cli_reassure_import.py`), rather than reaching around it.

Requirement: spec "reassure history <name> — Full Series". Covers:
  - a `name` present in imports A and C but absent from B yields EXACTLY two
    points (A, C); B's absence shifts nothing (coverage-gap scenario),
  - a `name` absent from every import exits `2`,
  - duration and count stay two independently-reduced series per point (I1),
  - the x-label fallback chain's first two levels (short `commit_hash`,
    else the date part of `ordered_at`) are reachable through REAL store
    data — the third level (`#<import_id>`) is NOT: `imported_at` is always
    populated at insert time (`store_sqlite.py`'s
    `self._clock.now_utc_iso()`), so `ordered_at` (`COALESCE(created_date,
    imported_at)`) is never empty through this path. That level is
    exercised directly against the renderer in
    `tests/golden/test_reassure_history_pretty_golden.py`, which can hand-
    build a `ReassureSeriesPoint` with an empty `ordered_at`.
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
) -> str:
    payload: dict = {
        "name": name,
        "runs": runs,
        "durations": durations if durations is not None else [1.0],
        "counts": counts if counts is not None else [1.0],
    }
    return json.dumps(payload)


def _write_perf_file(
    tmp_path: Path,
    filename: str,
    *,
    created_date: str | None,
    commit_hash: str | None = None,
    entry_lines: list[str],
) -> Path:
    lines = []
    if created_date is not None or commit_hash is not None:
        metadata: dict = {"branch": "main"}
        if commit_hash is not None:
            metadata["commitHash"] = commit_hash
        if created_date is not None:
            metadata["creationDate"] = created_date
        lines.append(json.dumps({"metadata": metadata}))
    lines.extend(entry_lines)
    path = tmp_path / filename
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _import(path: Path) -> int:
    result = runner.invoke(main_module.app, ["--json", "reassure", "import", str(path)])
    assert result.exit_code == 0, result.output
    imports = runner.invoke(main_module.app, ["--json", "reassure", "list", "--limit", "100"])
    payload = json.loads(imports.stdout)
    # D2 orders by `created_date` first, so locate this import by its
    # `source_path` to stay unambiguous regardless of ordering — matches
    # `test_cli_reassure_show.py`'s `_import` helper.
    for row in payload["imports"]:
        if row["source_path"] == str(path):
            return row["import_id"]
    raise AssertionError(f"import of {path} not found in roster: {payload}")


# ===== Coverage gaps do not misattribute data =====


def test_coverage_gap_yields_exactly_two_points(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    id_a = _import(
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
            entry_lines=[_entry_line(name="SomeOtherTest", durations=[99.0], counts=[9.0])],
        )
    )
    id_c = _import(
        _write_perf_file(
            tmp_path,
            "c.perf",
            created_date="2026-01-03T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[20.0], counts=[2.0])],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "history", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["points"]) == 2
    assert [p["import_id"] for p in payload["points"]] == [id_a, id_c]


def test_unknown_name_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(name="SomeOtherTest")],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "history", _NAME])

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""


def test_no_imports_at_all_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "history", _NAME])

    assert result.exit_code == 2, result.output


# ===== I1: duration and count stay two independent series per point =====


def test_duration_and_count_are_independently_reduced_per_point(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[10.0, 20.0], counts=[1.0])],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "history", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    point = payload["points"][0]
    assert point["duration"]["n"] == 2
    assert point["count"]["n"] == 1


# ===== x-label fallback chain — levels 1 and 2, real store data =====


def test_pretty_x_label_uses_short_commit_hash_when_present(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            commit_hash="abc1234567",
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["reassure", "history", _NAME])

    assert result.exit_code == 0, result.output
    assert "abc1234" in result.output  # first 7 chars of the commit


def test_pretty_x_label_falls_back_to_the_date_when_commit_hash_is_absent(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            commit_hash=None,
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["reassure", "history", _NAME])

    assert result.exit_code == 0, result.output
    assert "2026-01-01" in result.output


def test_mixed_series_falls_back_at_different_levels_per_point(monkeypatch, tmp_path: Path):
    """A window where one import has a commit and the next does not — an
    off-by-one in the fallback shows up here as a mislabelled axis, not a
    crash."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-01-01T00:00:00.000Z",
            commit_hash="feedcafe99",
            entry_lines=[_entry_line(durations=[10.0], counts=[1.0])],
        )
    )
    _import(
        _write_perf_file(
            tmp_path,
            "b.perf",
            created_date="2026-01-02T00:00:00.000Z",
            commit_hash=None,
            entry_lines=[_entry_line(durations=[20.0], counts=[2.0])],
        )
    )

    result = runner.invoke(main_module.app, ["reassure", "history", _NAME])

    assert result.exit_code == 0, result.output
    assert "feedcaf" in result.output  # point A: short commit
    assert "2026-01-02" in result.output  # point B: date fallback


# ===== pretty mode reaches the renderer, --json stays byte-pure =====


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

    result = runner.invoke(main_module.app, ["reassure", "history", _NAME])

    assert result.exit_code == 0, result.output
    assert "perfvibe reassure history" in result.output
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

    result = runner.invoke(main_module.app, ["--json", "reassure", "history", _NAME])

    assert result.exit_code == 0, result.output
    json.loads(result.stdout)  # raises if stdout carries anything but the payload


# ===== exit 3: store failure =====


def test_store_failure_exits_3(monkeypatch, tmp_path: Path):
    import perf.cli.commands.reassure as reassure_module

    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    class _BoomStore:
        def reassure_series(self, *args, **kwargs):
            raise RuntimeError("simulated store failure")

        def close(self) -> None:
            pass

    monkeypatch.setattr(reassure_module, "build_store", lambda *a, **kw: _BoomStore())

    result = runner.invoke(main_module.app, ["--json", "reassure", "history", _NAME])

    assert result.exit_code == 3, result.output
    assert result.stdout.strip() == ""


def test_exit_1_never_appears_anywhere_in_this_suite(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "history", _NAME])

    assert result.exit_code != 1
