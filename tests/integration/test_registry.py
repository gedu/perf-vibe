"""Registry tests (design §6): name-to-factory resolution; each adapter
kind is independently selectable/optional; an unknown name raises a clear
error before any adapter is constructed.

RED-before-GREEN: written before `src/perf/adapters/registry.py` existed.
"""

from __future__ import annotations

import pytest

from fakes import SequentialClock
from perf.adapters import registry
from perf.adapters.driver_maestro import MaestroDriver
from perf.adapters.driver_manual import ManualDriver
from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource
from perf.adapters.sampler_flashlight import FlashlightSampler
from perf.adapters.store_sqlite import SqliteStore


def test_build_driver_maestro_by_name():
    driver = registry.build_driver("maestro", known_flows={"checkout": "flows/checkout.yaml"})
    assert isinstance(driver, MaestroDriver)


def test_build_driver_manual_by_name():
    driver = registry.build_driver("manual", flow_prompts={})
    assert isinstance(driver, ManualDriver)


def test_build_driver_accepts_uniform_cli_kwargs_for_every_driver():
    """Regression (PR3 review, CRITICAL): the CLI builds ANY configured driver
    with the SAME common kwargs (known_flows + device + flow_prompts). Before
    the fix, `build_driver("manual", known_flows=..., device=...)` raised
    `TypeError` because ManualDriver's ctor takes neither — so `driver =
    "manual"` was completely broken end-to-end while every test called the
    registry with per-driver kwargs and missed it."""
    common = {
        "known_flows": {"checkout": "checkout.yaml"},
        "device": "emulator-5554",
        "flow_prompts": {},
    }
    assert isinstance(registry.build_driver("maestro", **common), MaestroDriver)
    assert isinstance(registry.build_driver("manual", **common), ManualDriver)


def test_build_sampler_flashlight_by_name():
    sampler = registry.build_sampler("flashlight")
    assert isinstance(sampler, FlashlightSampler)


def test_build_sampler_flashlight_threads_bundle_id_into_the_command():
    """Seam guard (regression): the `bundle_id` from config MUST reach the
    Flashlight invocation. `build_sampler` passes it through to the
    constructor, and `wrap()` emits it as `--bundleId` — without this the
    real binary aborts with 'required option --bundleId not specified'."""
    from perf.domain.model import DriverCommand

    sampler = registry.build_sampler("flashlight", bundle_id="com.example.app")
    inner = DriverCommand(argv=["maestro", "test", "flow.yaml"], automated=True)
    wrapped = sampler.wrap(inner, iterations=1, restart=False, results_path="out.json")

    assert "--bundleId" in wrapped.argv
    assert wrapped.argv[wrapped.argv.index("--bundleId") + 1] == "com.example.app"


def test_build_sampler_none_when_not_selected():
    assert registry.build_sampler(None) is None


def test_build_marker_source_adb_logcat_by_name():
    marker_source = registry.build_marker_source("adb-logcat")
    assert isinstance(marker_source, AdbLogcatMarkerSource)


def test_build_marker_source_none_when_not_selected():
    assert registry.build_marker_source(None) is None


def test_build_marker_source_threads_device_for_pinning():
    """Fix (resilience review): device pinning must be selectable through
    the registry, not just the constructor directly."""
    marker_source = registry.build_marker_source("adb-logcat", device="emulator-5554")
    assert isinstance(marker_source, AdbLogcatMarkerSource)
    assert marker_source.capture_spec().argv == [
        "adb",
        "-s",
        "emulator-5554",
        "logcat",
        "-s",
        "ReactNativeJS:V",
    ]


def test_build_analyzer_returns_sql_analyzer(tmp_path):
    """design 'Analyzer factory' decision: `build_analyzer(store, **params)`
    — single implementation, plain factory, no name-keyed map (rule of
    three), mirroring `build_store`."""
    from perf.adapters.analyzer_sql import SqlAnalyzer

    store = SqliteStore(tmp_path / "perf.db", clock=SequentialClock())
    try:
        analyzer = registry.build_analyzer(
            store,
            threshold_pct=5.0,
            floors={"ms": 5.0, "mb": 5.0, "pct": 3.0, "fps": 2.0},
            min_baseline_commits=3,
            warmup_k=1,
            baseline_n=10,
        )
        assert isinstance(analyzer, SqlAnalyzer)
    finally:
        store.close()


def test_build_commit_log_returns_git_commit_log():
    """budget-check design §10: `CommitLog` has exactly one implementation
    — a plain factory, no name-keyed map needed (mirrors
    `build_context_provider`/`build_store`)."""
    from perf.adapters.commit_log_git import GitCommitLog

    commit_log = registry.build_commit_log()
    assert isinstance(commit_log, GitCommitLog)


def test_build_commit_log_threads_repo_path_and_runner():
    from perf.adapters.commit_log_git import GitCommitLog
    from perf.adapters.process import SubprocessRunner

    runner = SubprocessRunner()
    commit_log = registry.build_commit_log(repo_path="/repo", runner=runner)
    assert isinstance(commit_log, GitCommitLog)


def test_unknown_driver_name_raises_clear_error():
    with pytest.raises(ValueError, match="maestro"):
        registry.build_driver("not-a-real-driver")


def test_unknown_sampler_name_raises_clear_error():
    with pytest.raises(ValueError, match="flashlight"):
        registry.build_sampler("not-a-real-sampler")


def test_unknown_marker_source_name_raises_clear_error():
    with pytest.raises(ValueError, match="adb-logcat"):
        registry.build_marker_source("not-a-real-marker-source")
