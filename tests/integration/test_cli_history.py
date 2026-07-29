"""End-to-end CLI harness for `perf history <flow>` — REAL `typer` app +
REAL `SqliteStore` (a real temp SQLite file) + REAL registry (SKILL rule:
"Every code path must be exercised through the REAL wiring at least once" —
the store is NEVER monkeypatched here). Only the `RunContextProvider` is
faked (device_key — never a live device/adb/git subprocess in a test),
mirroring `test_cli_compare.py`'s `_patch_context_provider` pattern.

Proves the full command dispatch, the `history_v1` `--json` contract,
exit-code discipline (0/2/3, NEVER 1), the `--metric`/`--limit`/`--restart`/
`--device-key` behavior, and the non-TTY stderr nudge (SKILL rule 6).
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

import perf.cli.commands.history as history_module
from perf.adapters.store_sqlite import SqliteStore
from perf.config.loader import FlowConfig, PerfConfig
from perf.domain.model import Marker, SystemSample

main_module = import_module("perf.cli.main")

from fakes import FakeRunContextProvider, SequentialClock, make_run_context  # noqa: E402

runner = CliRunner()

FLOW = "checkout"
DEVICE_KEY = "TestDevice|14|physical"


def _config(db_path: str) -> PerfConfig:
    return PerfConfig(
        db_path=db_path,
        no_color=True,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )


def _patch(monkeypatch, config: PerfConfig) -> None:
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    ctx = make_run_context(device_key=DEVICE_KEY, git_commit="c1")
    monkeypatch.setattr(
        history_module, "build_context_provider", lambda **kw: FakeRunContextProvider(ctx)
    )


def _seed(store, *, git_commit, checkout_ms, mode="warm", device_key=DEVICE_KEY):
    ctx = make_run_context(device_key=device_key, git_commit=git_commit, is_dev_bundle=False)
    markers = [
        Marker(name="checkout", value=checkout_ms + offset, unit="ms") for offset in (0, 10, 20)
    ]
    samples = [
        SystemSample(
            iteration_idx=idx,
            total_time_ms=None,
            start_time_ms=None,
            fps_avg=60.0 + idx,
            fps_min=None,
            ram_avg_mb=None,
            ram_peak_mb=None,
            cpu_avg_pct=None,
            cpu_peak_pct=None,
        )
        for idx in range(3)
    ]
    return store.save_run(ctx, FLOW, 3, mode, "local:test", markers, samples, None)


def _seed_history(db_path: Path, *, mode="warm", device_key=DEVICE_KEY, commits=("c1", "c2", "c3")):
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        for idx, commit in enumerate(commits):
            _seed(
                store,
                git_commit=commit,
                checkout_ms=100.0 + idx * 5,
                mode=mode,
                device_key=device_key,
            )
    finally:
        store.close()


def test_history_pretty_end_to_end_exits_0(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path)
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["history", "checkout"])

    assert result.exit_code == 0, result.output
    assert "checkout" in result.output
    assert "fps_avg" in result.output


def test_history_json_matches_contract_oldest_to_newest(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, commits=("c1", "c2", "c3"))
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["--json", "history", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["flow"] == "checkout"
    assert payload["device"] == DEVICE_KEY
    assert payload["mode"] == "warm"
    assert [run["commit"] for run in payload["runs"]] == ["c1", "c2", "c3"]
    first = payload["runs"][0]["metrics"]
    assert "checkout" in first and "fps_avg" in first
    assert first["fps_avg"]["unit"] == "fps"


def test_history_metric_filter_restricts_series(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path)
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(
        main_module.app, ["--json", "history", "checkout", "--metric", "fps_avg"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    for run in payload["runs"]:
        assert set(run["metrics"].keys()) == {"fps_avg"}


def test_history_unknown_metric_exits_2_lists_available(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path)
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["history", "checkout", "--metric", "nope"])

    assert result.exit_code == 2, result.output
    assert "checkout" in result.stderr  # an available metric name is listed
    assert "fps_avg" in result.stderr


def test_history_unknown_flow_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path)
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["history", "not-a-flow"])

    assert result.exit_code == 2, result.output


def test_history_no_history_at_all_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    SqliteStore(db_path).close()  # empty migrated DB — zero runs
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["history", "checkout"])

    assert result.exit_code == 2, result.output


def test_history_restart_charts_cold_series(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, mode="cold", commits=("c1", "c2"))
    _patch(monkeypatch, _config(str(db_path)))

    warm = runner.invoke(main_module.app, ["history", "checkout"])
    cold = runner.invoke(main_module.app, ["--json", "history", "checkout", "--restart"])

    assert warm.exit_code == 2  # no warm runs seeded
    assert cold.exit_code == 0, cold.output
    payload = json.loads(cold.stdout)
    assert payload["mode"] == "cold"
    assert len(payload["runs"]) == 2


def test_history_device_key_override_is_verbatim(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    other_key = "OtherDevice|13|physical"
    _seed_history(db_path, device_key=other_key, commits=("c1", "c2"))
    _patch(monkeypatch, _config(str(db_path)))

    # The faked provider yields DEVICE_KEY (no runs); the override targets the
    # seeded key verbatim.
    default = runner.invoke(main_module.app, ["history", "checkout"])
    override = runner.invoke(
        main_module.app, ["--json", "history", "checkout", "--device-key", other_key]
    )

    assert default.exit_code == 2  # DEVICE_KEY has no runs
    assert override.exit_code == 0, override.output
    payload = json.loads(override.stdout)
    assert payload["device"] == other_key
    assert len(payload["runs"]) == 2


def test_history_limit_clamped_to_at_least_one(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, commits=("c1", "c2", "c3"))
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["--json", "history", "checkout", "--limit", "0"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["runs"]) == 1  # clamped to 1, most recent
    assert payload["runs"][0]["commit"] == "c3"


def test_history_runtime_failure_on_corrupt_db_exits_3_never_1(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "garbage.db"
    db_path.write_bytes(b"this is definitely not a sqlite database file\x00\x01")
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["history", "checkout"])

    assert result.exit_code == 3, result.output
    assert "Error:" in result.stderr
    assert "Traceback" not in result.stderr


def test_history_non_tty_pretty_output_nudges_toward_json(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path)
    _patch(monkeypatch, _config(str(db_path)))

    result = runner.invoke(main_module.app, ["history", "checkout"])

    assert result.exit_code == 0
    assert "use --json" in result.output


def test_history_never_exits_1(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path)
    _patch(monkeypatch, _config(str(db_path)))

    seen = {
        runner.invoke(main_module.app, ["history", "checkout"]).exit_code,
        runner.invoke(main_module.app, ["history", "not-a-flow"]).exit_code,
        runner.invoke(main_module.app, ["history", "checkout", "--metric", "nope"]).exit_code,
    }
    assert 1 not in seen
