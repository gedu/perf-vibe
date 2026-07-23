"""`RunFlowUseCase` — PURE orchestration tests (design §1/§5, spec "Exit
Code Discipline"). Every port is faked (`tests/fakes.py`) — NO real
device, subprocess, or filesystem I/O. Covers the 4 `ExecutionPlan` shapes,
the minimum-measurement guard, every failure->exit-code mapping, the
zero-rows-on-failure guarantee, and the "never exit 1" contract.
"""

from __future__ import annotations

import pytest

from fakes import (
    FakeDriver,
    FakeMarkerSource,
    FakeRunContextProvider,
    FakeStore,
    FakeSystemSampler,
    FrozenClock,
    NoArgRunContextProvider,
    make_run_context,
)
from perf.application.run_flow import (
    RunFailedError,
    RunFlowRequest,
    RunFlowUseCase,
    UsageError,
)
from perf.domain.model import (
    DriverResult,
    Marker,
    MarkerParseResult,
    SystemSample,
    SystemSampleParseResult,
)


def _default_marker_source() -> FakeMarkerSource:
    # Non-empty by default so generic tests (not specifically about the
    # "no data captured" guard) exercise the success path without each
    # having to restate a marker fixture.
    return FakeMarkerSource(
        parse_result=MarkerParseResult(
            markers=(Marker(name="checkout", value=900.0, unit="ms"),),
            partial_coverage=False,
        )
    )


def _use_case(**overrides) -> RunFlowUseCase:
    defaults = {
        "driver": FakeDriver(),
        "sampler": FakeSystemSampler(),
        "marker_source": _default_marker_source(),
        "context_provider": FakeRunContextProvider(),
        "store": FakeStore(),
        "clock": FrozenClock(),
    }
    defaults.update(overrides)
    return RunFlowUseCase(**defaults)


def _request(**overrides) -> RunFlowRequest:
    defaults = {
        "flow_name": "checkout",
        "iterations": 3,
        "restart": False,
        "results_dir": "results",
    }
    defaults.update(overrides)
    return RunFlowRequest(**defaults)


# ===== The 4 ExecutionPlan shapes (design §1 table) =====


def test_shape_maestro_flashlight_and_markers_persists_both():
    markers = (Marker(name="checkout", value=900.0, unit="ms"),)
    samples = (
        SystemSample(
            iteration_idx=0,
            total_time_ms=1200.0,
            start_time_ms=300.0,
            fps_avg=58.0,
            fps_min=40.0,
            ram_avg_mb=500.0,
            ram_peak_mb=600.0,
            cpu_avg_pct=30.0,
            cpu_peak_pct=50.0,
        ),
    )
    driver = FakeDriver(automated=True)
    sampler = FakeSystemSampler(
        parse_result=SystemSampleParseResult(samples=samples, partial_coverage=False)
    )
    marker_source = FakeMarkerSource(
        parse_result=MarkerParseResult(markers=markers, partial_coverage=False)
    )
    store = FakeStore()

    use_case = _use_case(driver=driver, sampler=sampler, marker_source=marker_source, store=store)
    result = use_case.execute(_request())

    assert len(store.saved_runs) == 1
    saved = store.saved_runs[0]
    assert saved["markers"] == markers
    assert saved["samples"] == samples
    assert result.markers == markers
    assert result.samples == samples
    assert sampler.wrap_calls, "TOOL_MANAGED shape must wrap the inner command"
    assert sampler.parse_calls == [sampler.wrap_calls[0][3]]


def test_shape_maestro_without_flashlight_persists_markers_only():
    markers = (Marker(name="checkout", value=900.0, unit="ms"),)
    driver = FakeDriver(automated=True)
    marker_source = FakeMarkerSource(
        parse_result=MarkerParseResult(markers=markers, partial_coverage=False)
    )
    store = FakeStore()

    use_case = _use_case(driver=driver, sampler=None, marker_source=marker_source, store=store)
    result = use_case.execute(_request(results_dir=None))

    assert len(store.saved_runs) == 1
    assert store.saved_runs[0]["samples"] == ()
    assert result.samples == ()
    assert result.markers == markers


def test_shape_manual_driver_with_markers_persists_markers_only():
    markers = (Marker(name="checkout", value=1500.0, unit="ms"),)
    driver = FakeDriver(automated=False)
    marker_source = FakeMarkerSource(
        parse_result=MarkerParseResult(markers=markers, partial_coverage=False)
    )
    store = FakeStore()

    use_case = _use_case(driver=driver, sampler=None, marker_source=marker_source, store=store)
    result = use_case.execute(_request(results_dir=None))

    assert driver.drive_calls[0].inner.argv is None
    assert len(store.saved_runs) == 1
    assert result.markers == markers
    assert result.samples == ()


def test_shape_manual_driver_flashlight_seam_not_built_falls_back_to_markers():
    """Manual driver + Flashlight configured: `FlashlightSampler.wrap()`
    (the real adapter) returns `None` for a manual (unwrapped) inner
    command — a documented, unbuilt seam. The use-case must never attempt
    to parse a results artifact that was never produced, and must still
    persist successfully from markers alone."""

    markers = (Marker(name="checkout", value=1500.0, unit="ms"),)
    driver = FakeDriver(automated=False)
    sampler = FakeSystemSampler(wrap_returns_none=True)
    marker_source = FakeMarkerSource(
        parse_result=MarkerParseResult(markers=markers, partial_coverage=False)
    )
    store = FakeStore()

    use_case = _use_case(driver=driver, sampler=sampler, marker_source=marker_source, store=store)
    result = use_case.execute(_request())

    assert sampler.wrap_calls, "wrap() must still be attempted"
    assert not sampler.parse_calls, "parse() must never run when wrap() returned None"
    assert len(store.saved_runs) == 1
    assert store.saved_runs[0]["raw_report_path"] is None
    assert result.raw_report_path is None
    assert result.markers == markers


# ===== Minimum-measurement guard (usage error, exit 2, before any device touch) =====


def test_no_measurement_source_raises_usage_error_before_any_device_touch():
    driver = FakeDriver()
    use_case = _use_case(driver=driver, sampler=None, marker_source=None)

    with pytest.raises(UsageError):
        use_case.execute(_request(results_dir=None))

    assert not driver.commands_requested
    assert not driver.drive_calls


def test_sampler_without_results_dir_raises_usage_error():
    with pytest.raises(UsageError):
        _use_case(marker_source=None).execute(_request(results_dir=None))


def test_bad_flow_name_maps_to_usage_error_not_runtime_error():
    driver = FakeDriver(command_error=ValueError("unknown flow 'nope'"))
    store = FakeStore()
    use_case = _use_case(driver=driver, store=store)

    with pytest.raises(UsageError):
        use_case.execute(_request(flow_name="nope"))

    assert not store.saved_runs
    assert not driver.drive_calls


# ===== Runtime/tooling failures (exit 3), zero rows persisted =====


def test_device_offline_raises_run_failed_and_persists_nothing():
    driver = FakeDriver(drive_error=OSError("device offline"))
    store = FakeStore()
    use_case = _use_case(driver=driver, store=store)

    with pytest.raises(RunFailedError):
        use_case.execute(_request())

    assert not store.saved_runs


def test_driver_reports_failed_iterations_raises_run_failed():
    driver = FakeDriver(
        drive_result=DriverResult(
            ok=False, iteration_outcomes=("failed", "failed"), logcat_lines=()
        )
    )
    store = FakeStore()
    use_case = _use_case(driver=driver, store=store)

    with pytest.raises(RunFailedError):
        use_case.execute(_request())

    assert not store.saved_runs


def test_capture_failed_raises_run_failed_even_when_ok():
    driver = FakeDriver(
        drive_result=DriverResult(
            ok=True,
            iteration_outcomes=("ok", "ok"),
            logcat_lines=(),
            capture_failed=True,
            diagnostics="adb: more than one device/emulator",
        )
    )
    store = FakeStore()
    use_case = _use_case(driver=driver, store=store)

    with pytest.raises(RunFailedError) as excinfo:
        use_case.execute(_request())

    assert "more than one device" in (excinfo.value.diagnostics or "")
    assert not store.saved_runs


def test_sampler_parse_failure_maps_to_run_failed_without_importing_adapter_exception():
    """Simulates `FlashlightParseError` (a real adapter exception the
    use-case must NEVER import, per SKILL rule 1) with a bare
    `RuntimeError` — proves the mapping is generic, not name-specific."""

    sampler = FakeSystemSampler(parse_error=RuntimeError("status FAILURE"))
    store = FakeStore()
    use_case = _use_case(sampler=sampler, store=store)

    with pytest.raises(RunFailedError):
        use_case.execute(_request())

    assert not store.saved_runs


def test_no_data_captured_raises_run_failed():
    sampler = FakeSystemSampler(
        parse_result=SystemSampleParseResult(samples=(), partial_coverage=False)
    )
    marker_source = FakeMarkerSource(
        parse_result=MarkerParseResult(markers=(), partial_coverage=False)
    )
    store = FakeStore()
    use_case = _use_case(sampler=sampler, marker_source=marker_source, store=store)

    with pytest.raises(RunFailedError):
        use_case.execute(_request())

    assert not store.saved_runs


def test_store_failure_propagates_and_is_not_swallowed():
    store = FakeStore(save_error=RuntimeError("disk full"))
    marker_source = FakeMarkerSource(
        parse_result=MarkerParseResult(
            markers=(Marker(name="checkout", value=1.0),), partial_coverage=False
        )
    )
    use_case = _use_case(sampler=None, marker_source=marker_source, store=store)

    with pytest.raises(RuntimeError):
        use_case.execute(_request(results_dir=None))

    assert not store.saved_runs


# ===== Never exit 1 (SKILL rule 7) =====


def test_every_failure_path_raises_only_usage_or_run_failed_errors():
    scenarios = [
        _use_case(sampler=None, marker_source=None),
        _use_case(driver=FakeDriver(command_error=ValueError("bad flow"))),
        _use_case(driver=FakeDriver(drive_error=OSError("offline"))),
        _use_case(
            driver=FakeDriver(
                drive_result=DriverResult(ok=False, iteration_outcomes=("failed",), logcat_lines=())
            )
        ),
        _use_case(sampler=FakeSystemSampler(parse_error=RuntimeError("boom"))),
    ]
    for use_case in scenarios:
        with pytest.raises((UsageError, RunFailedError)):
            use_case.execute(_request())


# ===== Context assembly: mode derivation, source threading, fallback =====


def test_restart_flag_derives_cold_mode_and_threads_ctx_source():
    ctx = make_run_context(source="ci")
    store = FakeStore()
    use_case = _use_case(context_provider=FakeRunContextProvider(ctx), store=store)

    result = use_case.execute(_request(restart=True))

    assert result.mode == "cold"
    assert result.source == "ci"
    assert store.saved_runs[0]["mode"] == "cold"
    assert store.saved_runs[0]["source"] == "ci"


def test_warm_is_the_default_mode():
    result = _use_case().execute(_request(restart=False))
    assert result.mode == "warm"


def test_context_provider_without_logcat_lines_extension_still_works():
    """`NoArgRunContextProvider` only implements the bare Protocol
    signature (`context(self) -> RunContext`) — the use-case must fall
    back gracefully instead of raising `TypeError`."""

    provider = NoArgRunContextProvider()
    use_case = _use_case(context_provider=provider)

    result = use_case.execute(_request())

    assert provider.calls == 1
    assert result.device_key == provider._ctx.device_key


def test_context_provider_with_logcat_lines_extension_receives_captured_lines():
    provider = FakeRunContextProvider()
    marker_source = FakeMarkerSource(
        parse_result=MarkerParseResult(
            markers=(Marker(name="checkout", value=900.0, unit="ms"),),
            partial_coverage=False,
        )
    )
    driver = FakeDriver(
        drive_result=DriverResult(
            ok=True, iteration_outcomes=("ok",), logcat_lines=("[PERF-META] {}",)
        )
    )
    use_case = _use_case(driver=driver, marker_source=marker_source, context_provider=provider)

    use_case.execute(_request())

    assert provider.calls == [("[PERF-META] {}",)]


def test_results_path_is_filesystem_safe_and_uses_injected_clock():
    sampler = FakeSystemSampler()
    clock = FrozenClock(fixed="2026-01-02T03:04:05+00:00")
    use_case = _use_case(sampler=sampler, clock=clock)

    use_case.execute(_request(flow_name="checkout", results_dir="results"))

    results_path = sampler.wrap_calls[0][3]
    assert ":" not in results_path
    assert "checkout" in results_path
    assert results_path.startswith("results/")


def test_partial_coverage_flagged_when_either_source_reports_it():
    sampler = FakeSystemSampler(
        parse_result=SystemSampleParseResult(
            samples=(
                SystemSample(
                    iteration_idx=0,
                    total_time_ms=1.0,
                    start_time_ms=1.0,
                    fps_avg=1.0,
                    fps_min=1.0,
                    ram_avg_mb=1.0,
                    ram_peak_mb=1.0,
                    cpu_avg_pct=1.0,
                    cpu_peak_pct=1.0,
                ),
            ),
            partial_coverage=True,
        )
    )
    result = _use_case(sampler=sampler).execute(_request())
    assert result.partial_coverage is True


def test_all_iterations_forwarded_to_store_without_suppression():
    samples = tuple(
        SystemSample(
            iteration_idx=i,
            total_time_ms=1.0,
            start_time_ms=1.0,
            fps_avg=1.0,
            fps_min=1.0,
            ram_avg_mb=1.0,
            ram_peak_mb=1.0,
            cpu_avg_pct=1.0,
            cpu_peak_pct=1.0,
        )
        for i in range(3)
    )
    sampler = FakeSystemSampler(
        parse_result=SystemSampleParseResult(samples=samples, partial_coverage=False)
    )
    store = FakeStore()
    use_case = _use_case(sampler=sampler, store=store)
    use_case.execute(_request(iterations=3))
    assert len(store.saved_runs[0]["samples"]) == 3
