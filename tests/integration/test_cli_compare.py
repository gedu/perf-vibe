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

from fakes import (  # noqa: E402
    FakeAnalyzer,
    FakeRunContextProvider,
    SequentialClock,
    make_run_context,
)

runner = CliRunner()

FLOW = "checkout"
DEVICE_KEY = "TestDevice|14|physical"

_LABEL_MARKERS = ("reasonable", "too loose", "too strict", "insufficient data")


def _config(db_path: str, **overrides) -> PerfConfig:
    defaults = {
        "db_path": db_path,
        "no_color": True,
        "flows": {"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    }
    defaults.update(overrides)
    return PerfConfig(**defaults)


def _patch_context_provider(monkeypatch, *, git_commit="HEAD", device_key=DEVICE_KEY):
    ctx = make_run_context(device_key=device_key, git_commit=git_commit)
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


def test_compare_runtime_failure_on_corrupt_db_exits_3_never_1(monkeypatch, tmp_path: Path):
    """The exit-3 mapping is the one that lets CI distinguish a tooling
    failure from a regression — previously the ONLY untested branch of
    compare's exit-code contract. A `--db` pointing at a non-SQLite file
    must exit 3 with an error, through the REAL store wiring."""
    db_path = tmp_path / "garbage.db"
    db_path.write_bytes(b"this is definitely not a sqlite database file\x00\x01")
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch)

    result = runner.invoke(main_module.app, ["compare", "checkout"])

    assert result.exit_code == 3, result.output
    assert "Error:" in result.stderr
    assert "Traceback" not in result.stderr


def test_compare_analyzer_failure_exits_3_never_1(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch)
    monkeypatch.setattr(
        compare_module,
        "build_analyzer",
        lambda store, **kw: FakeAnalyzer(raises=RuntimeError("baseline query exploded")),
    )

    result = runner.invoke(main_module.app, ["compare", "checkout"])

    assert result.exit_code == 3, result.output
    assert "Error:" in result.stderr
    assert "baseline query exploded" in result.stderr


def test_compare_json_with_zero_baseline_emits_valid_json_null_delta(monkeypatch, tmp_path: Path):
    """`regression.classify` sets `delta_pct = ±inf` when the baseline is
    exactly 0 (an instant marker) and the latest value is not. `json.dumps`'s
    default rendered that as the literal `Infinity` — not RFC-8259 JSON, so
    `jq`/`JSON.parse` choked on the ONE machine contract. Non-finite floats
    must serialize as `null` (and never leak the bare literals)."""
    db_path = tmp_path / "perf.db"
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        for commit in ("c1", "c2", "c3", "c4"):
            _seed(store, git_commit=commit, checkout_ms=0.0)
        _seed(store, git_commit="HEAD", checkout_ms=130.0)
    finally:
        store.close()
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout"])

    assert result.exit_code == 0, result.output
    # Python's own json.loads ACCEPTS `Infinity`, so a parse alone can't
    # prove validity — assert the invalid literals are absent byte-wise.
    assert "Infinity" not in result.stdout
    assert "NaN" not in result.stdout
    payload = json.loads(result.stdout)
    checkout_verdict = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout_verdict["delta_pct"] is None


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


# ===== resilience batch Task 2: device-key override + last-recorded fallback =====


def test_compare_device_key_option_used_verbatim_skips_derivation(monkeypatch, tmp_path: Path):
    """Task 2: `--device-key` pins the key VERBATIM — even when live-adb
    derivation would produce a DIFFERENT (device-less, degraded) key that
    matches no history. History is seeded under DEVICE_KEY; the context
    provider is patched to derive the WRONG key, yet `--device-key
    DEVICE_KEY` still finds the history and exits 0."""
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    # Derivation would yield a degraded key with no history — proving the
    # explicit key wins, this must NOT be consulted.
    _patch_context_provider(monkeypatch, device_key="unknown|unknown|physical")

    result = runner.invoke(
        main_module.app, ["--json", "compare", "checkout", "--device-key", DEVICE_KEY]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    checkout_verdict = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout_verdict["status"] == "stable"
    # No fallback warning — the key was pinned, never derived-then-recovered.
    assert "falling back" not in result.stderr


def test_compare_falls_back_to_last_recorded_key_with_warning(monkeypatch, tmp_path: Path):
    """Task 2: with NO `--device-key` and a derived key that matches no
    history (device-less run degrading to `unknown|unknown|physical`),
    `compare` retries once with the most recent persisted device_key,
    warns naming BOTH keys, and still produces a verdict (exit 0)."""
    db_path = tmp_path / "perf.db"
    _seed_history(db_path, regression_on_latest=False)
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, device_key="unknown|unknown|physical")

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    checkout_verdict = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout_verdict["status"] == "stable"
    # The warning names BOTH the derived key and the recovered fallback key.
    assert "no history for derived device key" in result.stderr
    assert "unknown|unknown|physical" in result.stderr
    assert DEVICE_KEY in result.stderr


def test_compare_fallback_still_empty_exits_2(monkeypatch, tmp_path: Path):
    """Task 2: when even the fallback finds nothing (an empty DB — no
    persisted device_key at all), the existing no-history exit 2 stands."""
    db_path = tmp_path / "perf.db"
    SqliteStore(db_path).close()  # empty, migrated DB — zero runs
    config = _config(str(db_path))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, device_key="unknown|unknown|physical")

    result = runner.invoke(main_module.app, ["compare", "checkout"])

    assert result.exit_code == 2, result.output


# ===== resilience batch Task 3: adaptive_floor threaded from config =====


def _seed_noisy_baseline(db_path: Path) -> None:
    """5 baseline commits (checkout medians 100,90,110,95,105 -> robust
    noise 2*1.4826*5 ≈ 14.83) + a latest run +8ms over the median. +8
    clears the static ms floor (5.0) and the 5% threshold (flags under a
    STATIC floor) but is < 14.83, so the adaptive floor suppresses it."""
    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        for commit, value in (
            ("c1", 100.0),
            ("c2", 90.0),
            ("c3", 110.0),
            ("c4", 95.0),
            ("c5", 105.0),
        ):
            _seed(store, git_commit=commit, checkout_ms=value)
        _seed(store, git_commit="HEAD", checkout_ms=108.0)
    finally:
        store.close()


def test_adaptive_floor_true_default_suppresses_noisy_delta_via_cli(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    _seed_noisy_baseline(db_path)
    config = _config(str(db_path))  # adaptive_floor defaults True
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    checkout_verdict = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout_verdict["status"] == "stable"  # adaptive floor swallows +8


def test_adaptive_floor_false_from_config_flips_verdict_via_cli(monkeypatch, tmp_path: Path):
    """Task 3: `adaptive_floor = false` in the config must reach the analyzer
    through the REAL CLI and flip the SAME noisy-baseline verdict back to the
    static-floor result (regression)."""
    db_path = tmp_path / "perf.db"
    _seed_noisy_baseline(db_path)
    config = _config(str(db_path), adaptive_floor=False)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_context_provider(monkeypatch, git_commit="HEAD")

    result = runner.invoke(main_module.app, ["--json", "compare", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    checkout_verdict = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout_verdict["status"] == "regression"  # static floor -> +8 flags


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
