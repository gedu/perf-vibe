"""End-to-end CLI harness for `perf compare` — REAL `typer` app + REAL
`SqlAnalyzer`/`SqliteStore` (a real temp SQLite file) + REAL registry (SKILL
rule: "Every code path must be exercised through the REAL wiring at least
once" — the analyzer/store are NEVER monkeypatched here). Only the
`RunContextProvider` is faked (device_key/git_commit — never a live
device/adb/git subprocess in a test), mirroring `test_cli_run.py`'s
`_patch_registry` pattern.

Proves the full command dispatch, `--json` contract, exit-code discipline
(0/2/3, NEVER 1 — decision #53: `budget-check`'s exit 1 is deferred), the
corner cases C1 (first-ever run)/C2 (unknown flow), and the non-TTY stderr
nudge (SKILL rule 6/7). PR-C tasks 3.5/3.5a.
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

import perf.cli.commands.compare as compare_module
from perf.adapters.store_sqlite import SqliteStore
from perf.config.loader import FlowConfig, PerfConfig
from perf.domain.model import Marker, SystemSample

main_module = import_module("perf.cli.main")

from fakes import FakeRunContextProvider, SequentialClock, make_run_context  # noqa: E402

runner = CliRunner()

FLOW = "checkout"
DEVICE_KEY = "TestDevice|14|physical"

_LABEL_MARKERS = ("reasonable", "too loose", "too strict", "insufficient data")


def _config(db_path: str) -> PerfConfig:
    return PerfConfig(
        db_path=db_path,
        no_color=True,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )


def _patch_context_provider(monkeypatch, *, git_commit="HEAD"):
    ctx = make_run_context(device_key=DEVICE_KEY, git_commit=git_commit)
    monkeypatch.setattr(
        compare_module, "build_context_provider", lambda **kw: FakeRunContextProvider(ctx)
    )


def _seed(store, *, git_commit, checkout_ms):
    ctx = make_run_context(device_key=DEVICE_KEY, git_commit=git_commit, is_dev_bundle=False)
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
    return store.save_run(ctx, FLOW, 1, "warm", "local:test", markers, samples, None)


def _seed_history(db_path: Path, *, regression_on_latest: bool) -> None:
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        for commit in ("c1", "c2", "c3", "c4"):
            _seed(store, git_commit=commit, checkout_ms=100.0)
        latest_value = 130.0 if regression_on_latest else 100.0
        _seed(store, git_commit="HEAD", checkout_ms=latest_value)
    finally:
        store.close()


def test_compare_end_to_end_pretty_shows_stable_verdict_exits_0(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["compare", "checkout"])

    assert result.exit_code == 0, result.output
    assert "checkout" in result.output
    assert "STABLE" in result.output
    assert any(marker in result.output.lower() for marker in _LABEL_MARKERS)


def test_compare_end_to_end_json_matches_contract(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    checkout_verdict = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout_verdict["status"] == "stable"
    assert "calibration" in payload


def test_compare_real_regression_is_shown_and_still_exits_0(monkeypatch, tmp_path: Path):
    """spec 'Regression still exits 0' — a real, end-to-end-computed
    regression verdict is INFORMATIONAL in this slice (decision #53);
    `budget-check`'s exit 1 is a deferred follow-up."""
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    checkout_verdict = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout_verdict["status"] == "regression"

    pretty_result = runner.invoke(main_module.app, ["compare", "checkout"])
    assert pretty_result.exit_code == 0
    assert "REGRESSION" in pretty_result.output
    assert "!" in pretty_result.output


def test_compare_unknown_flow_exits_2(monkeypatch, tmp_path: Path):
    """C2: an unknown flow (not config-known) is a usage error."""
    db_path = tmp_path / "perf.db"
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch)

    result = runner.invoke(main_module.app, ["compare", "not-a-flow"])

    assert result.exit_code == 2, result.output


def test_compare_first_ever_run_of_known_flow_is_insufficient_data_exits_0(
    monkeypatch, tmp_path: Path
):
    """C1: a KNOWN flow's only run is the one being evaluated (no prior
    baseline) -> every metric `insufficient-data`, exit 0, NEVER 1."""
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        _seed(store, git_commit="HEAD", checkout_ms=100.0)
    finally:
        store.close()
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["verdicts"]) > 0
    assert all(v["status"] == "insufficient-data" for v in payload["verdicts"])


def test_compare_no_history_at_all_for_known_flow_exits_2(monkeypatch, tmp_path: Path):
    """A config-known flow with ZERO recorded runs -> usage error, exit 2
    (spec 'Unknown flow is a usage error' scenario: "a flow name with no
    history")."""
    db_path = tmp_path / "perf.db"
    SqliteStore(db_path).close()  # empty, migrated DB — zero runs
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["compare", "checkout"])

    assert result.exit_code == 2, result.output


def test_compare_sanity_label_present_in_pretty_and_json_never_changes_exit_code(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    pretty_result = runner.invoke(main_module.app, ["compare", "checkout"])
    json_result = runner.invoke(main_module.app, ["--json", "compare", "checkout"])

    assert pretty_result.exit_code == 0
    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    # `_seed_history` gives every baseline commit (c1..c4) the SAME
    # `checkout_ms`/`fps_avg` values (zero variance) — with the corrected
    # suppression-based `too-loose` definition (PR-C review fix), a
    # baseline that never crosses `threshold_pct` grades `reasonable`,
    # NOT `too-loose`, regardless of the excluded latest regression.
    assert payload["calibration"]["status"] == "reasonable"
    assert any(marker in pretty_result.output.lower() for marker in _LABEL_MARKERS)


def test_compare_non_tty_pretty_output_nudges_toward_json(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["compare", "checkout"])

    assert result.exit_code == 0
    assert "use --json" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["compare", "not-a-flow"],
        ["compare", "checkout"],
    ],
)
def test_compare_never_exits_1(monkeypatch, tmp_path: Path, args):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, args)

    assert result.exit_code != 1


def test_compare_exit_code_enumeration_never_1(monkeypatch, tmp_path: Path):
    """Exit-code discipline: enumerate every scenario this command can hit
    and assert the observed codes are exactly `{0, 2}` — NEVER `1` (spec
    'Exit-Code Discipline'; decision #53: exit 1 is DEFERRED to
    `budget-check`)."""
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=True)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    seen_codes = {
        runner.invoke(main_module.app, ["compare", "checkout"]).exit_code,
        runner.invoke(main_module.app, ["compare", "not-a-flow"]).exit_code,
    }

    assert seen_codes == {0, 2}
    assert 1 not in seen_codes
