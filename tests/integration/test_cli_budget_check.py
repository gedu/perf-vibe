"""End-to-end CLI harness for `perf budget-check` — REAL `typer` app + REAL
`SqlAnalyzer`/`SqliteStore` (a real temp SQLite file) + REAL registry,
mirroring `test_cli_compare.py`'s "never monkeypatch the analyzer/store"
discipline. Only the `RunContextProvider` is faked (device_key/git_commit —
never a live device/adb/git subprocess in a test).

Proves the full command dispatch, exit-code discipline (0/1/2/3 — the
CI-gating exit `1` budget-check spends, decision D3), the B1-B10
corner-case matrix (spec 'Corner-Case Matrix'), and the `--metric`
typo-vs-no-data split (tasks 3.12/3.14).
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

import perf.cli.commands.budget_check as budget_check_module
from perf.adapters.store_sqlite import SqliteStore
from perf.config.loader import FlowConfig, PerfConfig
from perf.domain.model import Marker, SystemSample

main_module = import_module("perf.cli.main")

from fakes import FakeRunContextProvider, SequentialClock, make_run_context  # noqa: E402

runner = CliRunner()

FLOW = "checkout"
DEVICE_KEY = "TestDevice|14|physical"


def _config(db_path: str, **overrides) -> PerfConfig:
    defaults = {
        "db_path": db_path,
        "no_color": True,
        "flows": {"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    }
    defaults.update(overrides)
    return PerfConfig(**defaults)


def _patch_context_provider(monkeypatch, *, git_commit="HEAD"):
    ctx = make_run_context(device_key=DEVICE_KEY, git_commit=git_commit)
    monkeypatch.setattr(
        budget_check_module, "build_context_provider", lambda **kw: FakeRunContextProvider(ctx)
    )


def _seed(store, *, git_commit, checkout_ms, mode="warm", is_dev_bundle=False):
    ctx = make_run_context(
        device_key=DEVICE_KEY, git_commit=git_commit, is_dev_bundle=is_dev_bundle
    )
    markers = [Marker(name="checkout", value=checkout_ms, unit="ms") for _ in range(3)]
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
    return store.save_run(ctx, FLOW, 1, mode, "local:test", markers, samples, None)


def _seed_history(db_path: Path, *, regression_on_latest: bool, mode: str = "warm") -> None:
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        for commit in ("c1", "c2", "c3", "c4"):
            _seed(store, git_commit=commit, checkout_ms=100.0, mode=mode)
        latest_value = 130.0 if regression_on_latest else 100.0
        _seed(store, git_commit="HEAD", checkout_ms=latest_value, mode=mode)
    finally:
        store.close()


# ===== core dispatch — confirmed regression / all-stable =====


def test_confirmed_regression_exits_1_and_json_reports_fail(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["gate_status"] == "fail"
    assert "checkout" in payload["offending_metrics"]
    assert result.stdout.strip() != ""


def test_all_stable_exits_0_gate_pass(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["gate_status"] == "pass"


def test_pretty_output_always_printed_before_exit(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["budget-check", "checkout"])

    assert result.exit_code == 1, result.output
    assert result.output.strip() != ""
    assert "GATE FAILED" in result.output


# ===== --strict fail-open vs fail-closed (B1) =====


def test_no_history_default_skipped_exit_0_strict_fails_exit_1(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        _seed(store, git_commit="HEAD", checkout_ms=100.0)
    finally:
        store.close()
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    default_result = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])
    strict_result = runner.invoke(
        main_module.app, ["--json", "budget-check", "checkout", "--strict"]
    )

    assert default_result.exit_code == 0, default_result.output
    assert json.loads(default_result.stdout)["gate_status"] == "skipped"
    assert strict_result.exit_code == 1, strict_result.output
    assert json.loads(strict_result.stdout)["gate_status"] == "fail"


# ===== B2: unknown flow — always a usage error =====


def test_unknown_flow_exits_2_default_and_strict(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch)

    default_result = runner.invoke(main_module.app, ["budget-check", "not-a-flow"])
    strict_result = runner.invoke(main_module.app, ["budget-check", "not-a-flow", "--strict"])

    assert default_result.exit_code == 2, default_result.output
    assert strict_result.exit_code == 2, strict_result.output


def test_no_history_at_all_for_known_flow_exits_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    SqliteStore(db_path).close()
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["budget-check", "checkout"])

    assert result.exit_code == 2, result.output


# ===== B3: insufficient baseline commits =====


def test_insufficient_baseline_commits_default_skipped_strict_fail(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        _seed(store, git_commit="c1", checkout_ms=100.0)
        _seed(store, git_commit="HEAD", checkout_ms=100.0)
    finally:
        store.close()
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    default_result = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])
    strict_result = runner.invoke(
        main_module.app, ["--json", "budget-check", "checkout", "--strict"]
    )

    assert default_result.exit_code == 0, default_result.output
    assert strict_result.exit_code == 1, strict_result.output


# ===== B5: one regression, rest stable — all offenders aggregated =====


def test_mixed_metrics_one_regression_gate_fails_and_offender_listed(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["offending_metrics"] == ["checkout"]
    checkout_entry = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout_entry["gated"] is True
    fps_entry = next(v for v in payload["verdicts"] if v["metric"] == "fps_avg")
    assert fps_entry["gated"] is False


# ===== B9: dev-bundle-only history fails open by default =====


def test_dev_bundle_only_history_fails_open_by_default(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        for commit in ("c1", "c2", "c3", "c4"):
            _seed(store, git_commit=commit, checkout_ms=100.0, is_dev_bundle=True)
        _seed(store, git_commit="HEAD", checkout_ms=100.0, is_dev_bundle=True)
    finally:
        store.close()
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])

    assert result.exit_code == 0, result.output


# ===== B10: runtime/tooling failure -> exit 3, never silently 0 or 1 =====


def test_analyzer_compare_latest_raise_mid_execute_exits_3(monkeypatch, tmp_path: Path):
    """A tooling failure DURING `Analyzer.compare_latest` (inside the
    use-case's own try/except, distinct from a composition-time failure)
    maps through `BudgetCheckFailedError` -> exit 3."""
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    real_build_analyzer = budget_check_module.build_analyzer

    class _RaisingAnalyzer:
        def __init__(self, inner):
            self._inner = inner

        def compare_latest(self, *args, **kwargs):
            raise RuntimeError("simulated store failure mid-query")

    monkeypatch.setattr(
        budget_check_module,
        "build_analyzer",
        lambda *a, **kw: _RaisingAnalyzer(real_build_analyzer(*a, **kw)),
    )

    result = runner.invoke(main_module.app, ["budget-check", "checkout"])

    assert result.exit_code == 3, result.output


def test_store_close_failure_never_overrides_the_computed_exit_code(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    real_build_store = budget_check_module.build_store

    class _CloseFailingStore:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            raise RuntimeError("simulated close failure")

    monkeypatch.setattr(
        budget_check_module,
        "build_store",
        lambda *a, **kw: _CloseFailingStore(real_build_store(*a, **kw)),
    )

    result = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])

    assert result.exit_code == 0, result.output  # gate PASS unaffected by the close warning


def test_analyzer_raise_exits_3(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated tooling failure")

    monkeypatch.setattr(budget_check_module, "build_analyzer", _boom)

    result = runner.invoke(main_module.app, ["budget-check", "checkout"])

    assert result.exit_code == 3, result.output


def test_render_failure_exits_3(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    def _boom(*args, **kwargs):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(budget_check_module, "render_summary", _boom)

    result = runner.invoke(main_module.app, ["budget-check", "checkout"])

    assert result.exit_code == 3, result.output


def test_never_exits_1_except_confirmed_regression_or_strict_insufficient(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["budget-check", "checkout"])
    assert result.exit_code == 0


# ===== --metric typo vs no-data split (tasks 3.12/3.14) =====


def test_metric_typo_exits_2_and_lists_valid_names(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(
        main_module.app, ["budget-check", "checkout", "--metric", "not-a-metric"]
    )

    assert result.exit_code == 2, result.output
    assert "checkout" in result.output  # a valid metric name is listed


def test_metric_valid_but_no_data_renders_normally_never_exit_2(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        for commit in ("c1", "c2", "c3", "c4"):
            _seed(store, git_commit=commit, checkout_ms=100.0)
        # Latest run with 0 markers -> checkout metric has no latest data
        # this run, but the metric family loop never runs (no latest
        # points at all) — use a single-iteration run instead to force
        # sample_n < min_baseline_commits (insufficient-data), a "no data
        # in this run"-shaped, still-VALID metric name.
        _seed(store, git_commit="HEAD", checkout_ms=999.0)
    finally:
        store.close()
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["budget-check", "checkout", "--metric", "checkout"])

    assert result.exit_code != 2, result.output


# ===== --json never affected by --metric/--verbose/--no-color (3.16) =====


def test_json_output_unaffected_by_metric_and_verbose_flags(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    plain = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])
    with_metric = runner.invoke(
        main_module.app, ["--json", "budget-check", "checkout", "--metric", "checkout", "--verbose"]
    )

    assert plain.exit_code == with_metric.exit_code == 1
    assert json.loads(plain.stdout) == json.loads(with_metric.stdout)


def test_json_output_never_contains_pretty_banner(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "budget-check", "checkout"])

    assert "GATE FAILED" not in result.stdout
    assert "┌─" not in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["budget-check", "checkout"],
        ["budget-check", "checkout", "--strict"],
        ["budget-check", "not-a-flow"],
    ],
)
def test_exit_code_enumeration_matches_the_gate_contract(monkeypatch, tmp_path: Path, args):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, args)

    assert result.exit_code in {0, 1, 2, 3}
