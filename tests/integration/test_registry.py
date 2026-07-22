"""Registry tests (design §6): name-to-factory resolution; each adapter
kind is independently selectable/optional; an unknown name raises a clear
error before any adapter is constructed.

RED-before-GREEN: written before `src/perf/adapters/registry.py` existed.
"""

from __future__ import annotations

import pytest

from perf.adapters import registry
from perf.adapters.driver_maestro import MaestroDriver
from perf.adapters.driver_manual import ManualDriver
from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource
from perf.adapters.sampler_flashlight import FlashlightSampler


def test_build_driver_maestro_by_name():
    driver = registry.build_driver("maestro", known_flows={"checkout": "flows/checkout.yaml"})
    assert isinstance(driver, MaestroDriver)


def test_build_driver_manual_by_name():
    driver = registry.build_driver("manual", flow_prompts={})
    assert isinstance(driver, ManualDriver)


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


def test_unknown_driver_name_raises_clear_error():
    with pytest.raises(ValueError, match="maestro"):
        registry.build_driver("not-a-real-driver")


def test_unknown_sampler_name_raises_clear_error():
    with pytest.raises(ValueError, match="flashlight"):
        registry.build_sampler("not-a-real-sampler")


def test_unknown_marker_source_name_raises_clear_error():
    with pytest.raises(ValueError, match="adb-logcat"):
        registry.build_marker_source("not-a-real-marker-source")
