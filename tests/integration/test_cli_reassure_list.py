"""End-to-end CLI harness for `perfvibe reassure list` — REAL `typer` app +
REAL `SqliteStore` (a real temp SQLite file) + REAL registry (python-testing
rule 3: "every code path must be exercised through the REAL wiring at
least once" — the store is NEVER monkeypatched here, matching
`test_cli_history.py`/`test_cli_reassure_import.py`). Only `load_config` is
faked, to avoid touching the real `~/.config/perf/config.toml`.

Seeds the store through `perfvibe reassure import` itself (the real
persistence path already covered end-to-end by
`test_cli_reassure_import.py`), rather than reaching around it — proves
`list` reads back exactly what `import` wrote.

Requirement: spec "reassure list — Import Roster" (D2 ordering, default
`--limit 50` matching `history`, an empty roster still exits `0`)."""

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


def _write_perf_file(
    tmp_path: Path, name: str, *, created_date: str | None, test_name: str
) -> Path:
    lines = []
    if created_date is not None:
        lines.append(
            json.dumps(
                {"metadata": {"branch": "main", "commitHash": "c1", "creationDate": created_date}}
            )
        )
    lines.append(json.dumps({"name": test_name, "runs": 1, "durations": [1.0], "counts": [1.0]}))
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _import(path: Path) -> None:
    result = runner.invoke(main_module.app, ["--json", "reassure", "import", str(path)])
    assert result.exit_code == 0, result.output


# ===== default --limit 50 (matching `history`, not `compare`) =====


def test_default_limit_is_50(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["reassure", "list", "--help"])

    assert result.exit_code == 0, result.output
    assert "[default: 50]" in result.output


def test_no_limit_flag_returns_every_seeded_import(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(tmp_path, "a.perf", created_date="2026-01-01T00:00:00.000Z", test_name="A")
    )
    _import(
        _write_perf_file(tmp_path, "b.perf", created_date="2026-01-02T00:00:00.000Z", test_name="B")
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "list"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert len(payload["imports"]) == 2


def test_explicit_limit_clamps_the_roster_to_the_most_recent_n(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(tmp_path, "a.perf", created_date="2026-01-01T00:00:00.000Z", test_name="A")
    )
    _import(
        _write_perf_file(tmp_path, "b.perf", created_date="2026-01-02T00:00:00.000Z", test_name="B")
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "list", "--limit", "1"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["imports"]) == 1
    assert payload["imports"][0]["created_date"] == "2026-01-02T00:00:00.000Z"


# ===== D2 ordering: created_date primary, imported_at fallback =====


def test_more_recent_created_date_sorts_first(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(tmp_path, "a.perf", created_date="2026-01-01T00:00:00.000Z", test_name="A")
    )
    _import(
        _write_perf_file(tmp_path, "b.perf", created_date="2026-01-02T00:00:00.000Z", test_name="B")
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "list"])

    payload = json.loads(result.stdout)
    dates = [row["created_date"] for row in payload["imports"]]
    assert dates == ["2026-01-02T00:00:00.000Z", "2026-01-01T00:00:00.000Z"]


def test_created_date_absent_falls_back_to_imported_at_without_crashing(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(_write_perf_file(tmp_path, "c.perf", created_date=None, test_name="C"))

    result = runner.invoke(main_module.app, ["--json", "reassure", "list"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["imports"][0]["created_date"] is None
    assert payload["imports"][0]["imported_at"]  # non-empty — the fallback ordering key


def test_ordering_key_is_not_a_payload_key(monkeypatch, tmp_path: Path):
    """`reassure_list_v1` deliberately excludes `ordering_key`/`ordered_at`
    (mechanically derivable from `created_date`/`imported_at` already in
    the payload) — see `contracts/reassure_list_v1.py`."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(tmp_path, "a.perf", created_date="2026-01-01T00:00:00.000Z", test_name="A")
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "list"])

    payload = json.loads(result.stdout)
    assert "ordering_key" not in payload["imports"][0]
    assert "ordered_at" not in payload["imports"][0]
    assert "kind" not in payload["imports"][0]


# ===== empty roster still exits 0 =====


def test_empty_roster_still_exits_0(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "list"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["imports"] == []


# ===== pretty mode reaches the renderer, `--json` stays byte-pure =====


def test_pretty_mode_reaches_the_renderer(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(tmp_path, "a.perf", created_date="2026-01-01T00:00:00.000Z", test_name="A")
    )

    result = runner.invoke(main_module.app, ["reassure", "list"])

    assert result.exit_code == 0, result.output
    assert "perfvibe reassure list" in result.output
    assert "imports" not in result.output  # no contract key leaked into the human view


def test_json_stdout_is_exactly_one_parseable_payload(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(tmp_path, "a.perf", created_date="2026-01-01T00:00:00.000Z", test_name="A")
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "list"])

    assert result.exit_code == 0, result.output
    json.loads(result.stdout)  # raises if stdout carries anything but the payload


# ===== exit 3: store failure =====


def test_store_failure_exits_3(monkeypatch, tmp_path: Path):
    """Mirrors `test_cli_reassure_import.py::test_store_failure_exits_3`:
    an unexpected store failure is a runtime/tooling error (exit `3`),
    never exit `1` (SKILL rule 7)."""
    import perf.cli.commands.reassure as reassure_module

    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    class _BoomStore:
        def reassure_imports(self, *args, **kwargs):
            raise RuntimeError("simulated store failure")

        def close(self) -> None:
            pass

    monkeypatch.setattr(reassure_module, "build_store", lambda *a, **kw: _BoomStore())

    result = runner.invoke(main_module.app, ["--json", "reassure", "list"])

    assert result.exit_code == 3, result.output
    assert result.stdout.strip() == ""
