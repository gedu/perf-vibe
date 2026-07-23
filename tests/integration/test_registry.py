"""Registry tests (design §6): name-to-factory resolution; each adapter
kind is independently selectable/optional; an unknown name raises a clear
error before any adapter is constructed.

RED-before-GREEN: written before `src/perf/adapters/registry.py` existed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from fakes import SequentialClock  # noqa: E402

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
    common = {"known_flows": {"checkout": "checkout.yaml"}, "device": "emulator-5554", "flow_prompts": {}}
    assert isinstance(registry.build_driver("maestro", **common), MaestroDriver)
    assert isinstance(registry.build_driver("manual", **common), ManualDriver)


def test_build_sampler_flashlight_by_name():
    sampler = registry.build_sampler("flashlight")
    assert isinstance(sampler, FlashlightSampler)


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
        "adb", "-s", "emulator-5554", "logcat", "-s", "ReactNativeJS:V",
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


def test_unknown_driver_name_raises_clear_error():
    with pytest.raises(ValueError, match="maestro"):
        registry.build_driver("not-a-real-driver")


def test_unknown_sampler_name_raises_clear_error():
    with pytest.raises(ValueError, match="flashlight"):
        registry.build_sampler("not-a-real-sampler")


def test_unknown_marker_source_name_raises_clear_error():
    with pytest.raises(ValueError, match="adb-logcat"):
        registry.build_marker_source("not-a-real-marker-source")
