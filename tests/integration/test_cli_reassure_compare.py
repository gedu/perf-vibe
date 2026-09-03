"""End-to-end CLI harness for `perfvibe reassure compare <name>` — REAL
`typer` app + REAL `SqliteStore` (a real temp SQLite file) + REAL registry
(python-testing rule 3: "every code path must be exercised through the REAL
wiring at least once" — the store is NEVER monkeypatched here, matching
`test_cli_reassure_history.py`/`test_cli_reassure_show.py`). Only
`load_config` is faked, to avoid touching the real
`~/.config/perf/config.toml`.

Seeds the store through `perfvibe reassure import` itself (the real
persistence path already covered end-to-end by
`test_cli_reassure_import.py`), rather than reaching around it.

**THE LOAD-BEARING CONTRACT THIS FILE PINS (D3)**: `reassure compare`
ALWAYS exits `0` — including on a confirmed regression. The exit code
carries NO verdict information; an agent (or a CI script) that treats a
non-zero exit here as "regression found" will NEVER catch a real one,
because this exit code never differs on that basis. Only an unknown
`name` (`2`) and a store/render failure (`3`) ever differ. See `AGENTS.md`
and `CLAUDE.md` for the agent-facing warning this test exists to pin.

Requirement: spec "reassure compare <name> — Baseline Verdict (D3, D7)".
Covers:
  - a confirmed `render_count` regression still exits `0` (the contract),
  - a one-import `name` (no baseline window at all) exits `0` with an
    EXPLICIT `insufficient-data` state per verdict — never a silent
    `stable`,
  - an unknown `name` exits `2`,
  - A8: the baseline window is `store.reassure_series(name, config.
    baseline_n + 1)` — a `baseline_n` override changes how many imports
    land in the window.
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
    # THREE samples by default, never one: `regression.classify`'s own
    # `sample_n < min_n` guard (`MIN_BASELINE_IMPORTS = 3`) reads the
    # LATEST import's `HistoryMetric.n`, so a single-sample latest entry
    # forces `insufficient-data` regardless of how the values differ — a
    # trap that cost one iteration writing the regression tests below.
    payload: dict = {
        "name": name,
        "runs": runs,
        "durations": durations if durations is not None else [10.0, 10.0, 10.0],
        "counts": counts if counts is not None else [1.0, 1.0, 1.0],
    }
    return json.dumps(payload)


def _write_perf_file(
    tmp_path: Path,
    filename: str,
    *,
    created_date: str,
    entry_lines: list[str],
) -> Path:
    lines = [
        json.dumps({"metadata": {"branch": "main", "creationDate": created_date}}),
        *entry_lines,
    ]
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
    # `test_cli_reassure_history.py`'s `_import` helper.
    for row in payload["imports"]:
        if row["source_path"] == str(path):
            return row["import_id"]
    raise AssertionError(f"import of {path} not found in roster: {payload}")


# ===== D3: ALWAYS exit 0, including on a confirmed regression =====


def test_confirmed_regression_still_exits_0(monkeypatch, tmp_path: Path):
    """[unmissable] THE contract this file exists to pin: `reassure compare`
    reports a regression, it never gates."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    for day in range(1, 4):
        _import(
            _write_perf_file(
                tmp_path,
                f"baseline{day}.perf",
                created_date=f"2026-01-0{day}T00:00:00.000Z",
                entry_lines=[_entry_line(durations=[10.0, 10.0, 10.0], counts=[1.0, 1.0, 1.0])],
            )
        )
    _import(
        _write_perf_file(
            tmp_path,
            "latest.perf",
            created_date="2026-01-04T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[10.0, 10.0, 10.0], counts=[9.0, 9.0, 9.0])],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "compare", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    verdicts_by_metric = {v["metric"]: v for v in payload["verdicts"]}
    assert verdicts_by_metric["render_count"]["status"] == "regression"
    assert verdicts_by_metric["duration_ms"]["status"] == "stable"


def test_pretty_regression_still_exits_0(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    for day in range(1, 4):
        _import(
            _write_perf_file(
                tmp_path,
                f"baseline{day}.perf",
                created_date=f"2026-02-0{day}T00:00:00.000Z",
                entry_lines=[_entry_line(durations=[10.0, 10.0, 10.0], counts=[1.0, 1.0, 1.0])],
            )
        )
    _import(
        _write_perf_file(
            tmp_path,
            "latest.perf",
            created_date="2026-02-04T00:00:00.000Z",
            entry_lines=[_entry_line(durations=[10.0, 10.0, 10.0], counts=[9.0, 9.0, 9.0])],
        )
    )

    result = runner.invoke(main_module.app, ["reassure", "compare", _NAME])

    assert result.exit_code == 0, result.output
    assert "REGRESSION" in result.output


# ===== insufficient data, explicit, never a silent "stable" =====


def test_one_import_name_exits_0_with_explicit_insufficient_data(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "only.perf",
            created_date="2026-03-01T00:00:00.000Z",
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "compare", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["baseline_import_n"] == 0
    for verdict in payload["verdicts"]:
        assert verdict["status"] == "insufficient-data"


def test_one_import_name_pretty_shows_insufficient_data_not_stable(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "only.perf",
            created_date="2026-03-02T00:00:00.000Z",
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["reassure", "compare", _NAME])

    assert result.exit_code == 0, result.output
    # The box only — `NON_TTY_NUDGE` (a shared, pre-existing stderr line
    # unrelated to this command) happens to contain the substring
    # "stable", so this scopes the negative to the rendered table itself.
    box = result.output.split("┌─", 1)[1]
    assert "insufficient-data" in box.lower()
    assert "stable" not in box.lower()


# ===== unknown name =====


def test_unknown_name_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-04-01T00:00:00.000Z",
            entry_lines=[_entry_line(name="SomeOtherTest")],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "compare", _NAME])

    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == ""


def test_no_imports_at_all_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "compare", _NAME])

    assert result.exit_code == 2, result.output


# ===== A8: baseline window is config.baseline_n + 1 =====


def test_baseline_window_uses_configured_baseline_n(monkeypatch, tmp_path: Path):
    """A8: `store.reassure_series(name, config.baseline_n + 1)` — with
    `baseline_n=2`, a 4th (oldest) import must fall OUTSIDE the window, so
    `baseline_import_n` never exceeds 2."""
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path), baseline_n=2)
    for day in range(1, 4):
        _import(
            _write_perf_file(
                tmp_path,
                f"import{day}.perf",
                created_date=f"2026-05-0{day}T00:00:00.000Z",
                entry_lines=[_entry_line()],
            )
        )
    _import(
        _write_perf_file(
            tmp_path,
            "latest.perf",
            created_date="2026-05-04T00:00:00.000Z",
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "compare", _NAME])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["baseline_import_n"] == 2


# ===== pretty mode reaches the renderer, --json stays byte-pure =====


def test_pretty_mode_reaches_the_renderer(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-06-01T00:00:00.000Z",
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["reassure", "compare", _NAME])

    assert result.exit_code == 0, result.output
    assert "perfvibe reassure compare" in result.output
    assert "schema_version" not in result.output


def test_json_stdout_is_exactly_one_parseable_payload(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))
    _import(
        _write_perf_file(
            tmp_path,
            "a.perf",
            created_date="2026-06-02T00:00:00.000Z",
            entry_lines=[_entry_line()],
        )
    )

    result = runner.invoke(main_module.app, ["--json", "reassure", "compare", _NAME])

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

    result = runner.invoke(main_module.app, ["--json", "reassure", "compare", _NAME])

    assert result.exit_code == 3, result.output
    assert result.stdout.strip() == ""


def test_exit_1_never_appears_anywhere_in_this_suite(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _patch_load_config(monkeypatch, db_path=str(db_path))

    result = runner.invoke(main_module.app, ["--json", "reassure", "compare", _NAME])

    assert result.exit_code != 1
