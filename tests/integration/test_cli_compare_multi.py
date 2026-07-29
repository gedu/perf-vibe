"""End-to-end CLI harness for the MULTI-flow / `--all` / interactive-picker
`perf compare` forms — REAL `typer` app + REAL `SqlAnalyzer`/`SqliteStore`
(a real temp SQLite file) + REAL registry (SKILL rule 3: exercise the real
wiring; only the `RunContextProvider` and the terminal picker boundary are
faked — never a live device or a real TTY).

Proves: multiple flow args, `--all`, the mutual-exclusion usage error, the
`compare_all_v1` `--json` envelope, no-history skip-with-warning (and the
all-empty exit-2), unknown-flow upfront rejection, and the no-arg
interactive-picker gating (non-TTY/`--json` -> exit 2; TTY -> picker; cancel
-> exit 0; raw-mode unavailable -> exit-2 fallback). Exit discipline stays
0/2/3, NEVER 1.
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

import perf.cli.commands.compare as compare_module
from perf.adapters.store_sqlite import SqliteStore
from perf.cli.output.flow_picker_terminal import PickerUnavailable
from perf.config.loader import FlowConfig, PerfConfig
from perf.domain.model import Marker, SystemSample

main_module = import_module("perf.cli.main")

from fakes import (  # noqa: E402
    FakeRunContextProvider,
    SequentialClock,
    make_run_context,
)

runner = CliRunner()

DEVICE_KEY = "TestDevice|14|physical"


def _config(db_path: str) -> PerfConfig:
    return PerfConfig(
        db_path=db_path,
        no_color=True,
        flows={
            "checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml"),
            "login": FlowConfig(name="login", maestro_path="login.yaml"),
        },
    )


def _patch_context_provider(monkeypatch, *, git_commit="HEAD"):
    ctx = make_run_context(device_key=DEVICE_KEY, git_commit=git_commit)
    monkeypatch.setattr(
        compare_module, "build_context_provider", lambda **kw: FakeRunContextProvider(ctx)
    )


def _seed(store, *, flow, git_commit, value):
    ctx = make_run_context(device_key=DEVICE_KEY, git_commit=git_commit, is_dev_bundle=False)
    markers = [Marker(name=flow, value=value, unit="ms") for _ in range(3)]
    samples = [
        SystemSample(
            iteration_idx=idx,
            total_time_ms=None,
            start_time_ms=None,
            fps_avg=60.0,
            fps_min=None,
            ram_avg_mb=None,
            ram_peak_mb=None,
            cpu_avg_pct=None,
            cpu_peak_pct=None,
        )
        for idx in range(2)
    ]
    return store.save_run(ctx, flow, 1, "warm", "local:test", markers, samples, None)


def _seed_flow_history(db_path: Path, flow: str) -> None:
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        for commit in ("c1", "c2", "c3", "c4"):
            _seed(store, flow=flow, git_commit=commit, value=100.0)
        _seed(store, flow=flow, git_commit="HEAD", value=100.0)
    finally:
        store.close()


def _wire(monkeypatch, config):
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")


# ---- multiple flow arguments ------------------------------------------------


def test_two_flows_pretty_renders_both_with_headers_exits_0(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _seed_flow_history(db_path, "login")
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["compare", "checkout", "login"])

    assert result.exit_code == 0, result.output
    assert "checkout" in result.output
    assert "login" in result.output
    # Each flow gets its own header line.
    assert result.output.count("═══") >= 2


def test_two_flows_json_uses_compare_all_envelope(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _seed_flow_history(db_path, "login")
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout", "login"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    flows = {entry["flow"]: entry for entry in payload["flows"]}
    assert set(flows) == {"checkout", "login"}
    assert flows["checkout"]["result"]["schema_version"] == 1


def test_all_flag_compares_every_flow_sorted(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _seed_flow_history(db_path, "login")
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["--json", "compare", "--all"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [entry["flow"] for entry in payload["flows"]] == ["checkout", "login"]


def test_all_and_explicit_flow_is_usage_error_exit_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["compare", "--all", "checkout"])

    assert result.exit_code == 2, result.output
    assert "Error:" in result.stderr


def test_multi_skips_no_history_flow_with_warning_exit_0(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")  # login has NO history
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["compare", "checkout", "login"])

    assert result.exit_code == 0, result.output
    assert "warning:" in result.stderr
    assert "login" in result.stderr
    assert "checkout" in result.output


def test_multi_json_skipped_flow_is_an_error_entry(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout", "login"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    flows = {entry["flow"]: entry for entry in payload["flows"]}
    assert flows["login"] == {"flow": "login", "error": "no-history"}
    assert "result" in flows["checkout"]


def test_multi_all_no_history_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    SqliteStore(db_path).close()  # empty migrated DB
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["compare", "checkout", "login"])

    assert result.exit_code == 2, result.output


def test_multi_unknown_flow_checked_upfront_exit_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["compare", "checkout", "bogus"])

    assert result.exit_code == 2, result.output
    assert "bogus" in result.stderr


def test_multi_never_exits_1(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))

    for args in (
        ["compare", "checkout", "login"],
        ["compare", "--all"],
        ["compare", "--all", "checkout"],
        ["--json", "compare", "checkout", "login"],
    ):
        assert runner.invoke(main_module.app, args).exit_code != 1


# ---- no-arg interactive picker gating --------------------------------------


def test_no_args_non_tty_is_usage_error_with_hint_exit_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["compare"])

    assert result.exit_code == 2, result.output
    assert "--all" in result.stderr


def test_no_args_json_is_usage_error_exit_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["--json", "compare"])

    assert result.exit_code == 2, result.output


def test_no_args_tty_invokes_picker_and_runs_selection(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))
    # Fake ONLY the terminal boundary: the picker is "available" and returns
    # a chosen flow; the compare then runs for real through the analyzer.
    monkeypatch.setattr(compare_module, "_picker_available", lambda output: True)
    monkeypatch.setattr(compare_module, "pick_flows", lambda flows, *, color: ["checkout"])

    result = runner.invoke(main_module.app, ["compare"])

    assert result.exit_code == 0, result.output
    assert "STABLE" in result.output


def test_picker_cancel_exits_0_with_notice(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))
    monkeypatch.setattr(compare_module, "_picker_available", lambda output: True)
    monkeypatch.setattr(compare_module, "pick_flows", lambda flows, *, color: None)

    result = runner.invoke(main_module.app, ["compare"])

    assert result.exit_code == 0, result.output
    assert "no flow selected" in result.stderr


def test_picker_unavailable_falls_back_to_usage_error_exit_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _wire(monkeypatch, _config(str(db_path)))
    monkeypatch.setattr(compare_module, "_picker_available", lambda output: True)

    def _raise(flows, *, color):
        raise PickerUnavailable("no tty")

    monkeypatch.setattr(compare_module, "pick_flows", _raise)

    result = runner.invoke(main_module.app, ["compare"])

    assert result.exit_code == 2, result.output
    assert "--all" in result.stderr


def test_picker_multi_selection_uses_multi_flow_view(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_flow_history(db_path, "checkout")
    _seed_flow_history(db_path, "login")
    _wire(monkeypatch, _config(str(db_path)))
    monkeypatch.setattr(compare_module, "_picker_available", lambda output: True)
    monkeypatch.setattr(compare_module, "pick_flows", lambda flows, *, color: ["checkout", "login"])

    result = runner.invoke(main_module.app, ["compare"])

    assert result.exit_code == 0, result.output
    assert result.output.count("═══") >= 2
