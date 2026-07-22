"""Pure-domain unit tests for `perf.domain.model` (Rev 2 — tasks 2.3 RED /
2.4 GREEN).

No I/O, no adapters — only dataclass construction, immutability, and pure
ExecutionPlan composition (design §1 steps 5-7) + direction defaults
(decision #39).
"""

from __future__ import annotations

import dataclasses

import pytest

from perf.domain.model import (
    CaptureSpec,
    Device,
    DriverCommand,
    DriverResult,
    ExecutionPlan,
    Flow,
    LoopMode,
    Marker,
    MarkerParseResult,
    Measure,
    Metric,
    Run,
    RunContext,
    SamplerCommand,
    SystemSample,
    Verdict,
    compose_execution_plan,
    default_higher_is_better,
)


def _run_context(**overrides) -> RunContext:
    defaults = dict(
        device_key="Pixel 8 Pro|Android 14|physical",
        model="Pixel 8 Pro",
        os_version="Android 14",
        is_emulator=False,
        source="local:eduardo",
        git_commit="abc123",
        git_branch="main",
        app_version="1.2.3",
        is_dev_bundle=False,
        bundle_source="embedded",
        build_variant="release",
        tool_version="0.1.0",
    )
    defaults.update(overrides)
    return RunContext(**defaults)


def _system_sample(**overrides) -> SystemSample:
    defaults = dict(
        iteration_idx=0,
        total_time_ms=46712.0,
        start_time_ms=1342.0,
        fps_avg=59.28,
        fps_min=55.0,
        ram_avg_mb=210.5,
        ram_peak_mb=240.0,
        cpu_avg_pct=12.4,
        cpu_peak_pct=30.0,
    )
    defaults.update(overrides)
    return SystemSample(**defaults)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Device(device_key="Pixel 8 Pro|Android 14|physical", model="Pixel 8 Pro", os_version="Android 14"),
        lambda: Flow(name="prestamos-warm"),
        lambda: Metric(name="fps_avg"),
        lambda: Marker(name="checkout", value=900.0, unit="ms"),
        _system_sample,
        _run_context,
        lambda: Run(
            flow_name="prestamos-warm",
            device_key="Pixel 8 Pro|Android 14|physical",
            started_at="2026-07-22T00:00:00Z",
            iterations=10,
            mode="warm",
            context=_run_context(),
        ),
        lambda: Measure(metric_name="/loans/details/:id", duration_ms=900.0),
        lambda: Verdict(metric_name="/loans/details/:id", delta_pct=5.0, threshold_pct=10.0, status="stable"),
        lambda: DriverCommand(argv=["maestro", "test", "prestamos-warm"], automated=True),
        lambda: DriverCommand(argv=None, automated=False, prompt="Run the flow manually, then confirm."),
        lambda: SamplerCommand(
            argv=["flashlight", "test", "--testCommand", "maestro test prestamos-warm"],
            results_path="/tmp/results/prestamos-warm.json",
            manages_iterations=True,
        ),
        lambda: CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"]),
        lambda: DriverResult(ok=True, iteration_outcomes=["success"], logcat_lines=["[PERF] checkout: 900ms"]),
        lambda: MarkerParseResult(markers=(Marker(name="checkout", value=900.0, unit="ms"),), partial_coverage=False),
    ],
)
def test_value_objects_are_frozen_dataclasses(factory):
    instance = factory()
    assert dataclasses.is_dataclass(instance)
    assert dataclasses.fields(instance)[0]  # constructed with at least one field

    field_name = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, getattr(instance, field_name))


def test_device_defaults_is_emulator_false():
    device = Device(device_key="k", model="m", os_version="os")
    assert device.is_emulator is False


def test_metric_defaults_unit_ms_and_lower_is_better():
    metric = Metric(name="/loans/details/:id")
    assert metric.unit == "ms"
    assert metric.higher_is_better is False


def test_metric_can_be_marked_higher_is_better():
    metric = Metric(name="fps_avg", higher_is_better=True)
    assert metric.higher_is_better is True


def test_run_run_id_and_raw_report_path_default_to_none():
    run = Run(
        flow_name="prestamos-warm",
        device_key="Pixel 8 Pro|Android 14|physical",
        started_at="2026-07-22T00:00:00Z",
        iterations=10,
        mode="warm",
        context=_run_context(),
    )
    assert run.run_id is None
    assert run.raw_report_path is None


def test_run_carries_raw_report_path_when_a_sampler_ran():
    run = Run(
        flow_name="prestamos-warm",
        device_key="Pixel 8 Pro|Android 14|physical",
        started_at="2026-07-22T00:00:00Z",
        iterations=10,
        mode="warm",
        context=_run_context(),
        raw_report_path="/tmp/results/prestamos-warm-20260722.json",
    )
    assert run.raw_report_path == "/tmp/results/prestamos-warm-20260722.json"


def test_run_context_is_dev_bundle_originates_only_from_perf_meta():
    ctx = _run_context(is_dev_bundle=None)
    assert ctx.is_dev_bundle is None  # unknown/null, never guessed


def test_marker_holds_arbitrary_metric_name_no_hardcoded_route():
    marker = Marker(name="checkout", value=900.0, unit="ms")
    assert marker.name == "checkout"
    assert marker.value == 900.0
    assert marker.unit == "ms"


def test_marker_unit_defaults_ms():
    marker = Marker(name="anything", value=1.0)
    assert marker.unit == "ms"


def test_system_sample_has_no_network_fields_and_carries_full_rev2_shape():
    sample = _system_sample()
    field_names = {f.name for f in dataclasses.fields(sample)}
    assert not any("net" in name for name in field_names)
    assert field_names == {
        "iteration_idx",
        "total_time_ms",
        "start_time_ms",
        "fps_avg",
        "fps_min",
        "ram_avg_mb",
        "ram_peak_mb",
        "cpu_avg_pct",
        "cpu_peak_pct",
    }


def test_system_sample_metric_fields_are_optional_for_empty_measures():
    """Empty `measures[]` still yields time/startTime but no fps/ram/cpu
    (design §3 — Flashlight ingestion, empty-measures scenario)."""
    sample = SystemSample(
        iteration_idx=0,
        total_time_ms=1000.0,
        start_time_ms=50.0,
        fps_avg=None,
        fps_min=None,
        ram_avg_mb=None,
        ram_peak_mb=None,
        cpu_avg_pct=None,
        cpu_peak_pct=None,
    )
    assert sample.total_time_ms == 1000.0
    assert sample.fps_avg is None


# ===== Direction defaults (decision #39) =====


@pytest.mark.parametrize("metric_name", ["fps_avg", "fps_min"])
def test_default_higher_is_better_true_for_fps_metrics(metric_name):
    assert default_higher_is_better(metric_name) is True


@pytest.mark.parametrize(
    "metric_name",
    [
        "total_time_ms",
        "start_time_ms",
        "ram_avg_mb",
        "ram_peak_mb",
        "cpu_avg_pct",
        "cpu_peak_pct",
        "checkout",  # arbitrary marker duration name — durations default lower-is-better
        "/loans/details/:id",
    ],
)
def test_default_higher_is_better_false_for_everything_else(metric_name):
    assert default_higher_is_better(metric_name) is False


# ===== ExecutionPlan composition (design §1 steps 5-7; the 4 supported shapes) =====


def test_execution_plan_maestro_flashlight_markers_is_tool_managed():
    """Shape (a): Flashlight wraps Maestro and owns the iteration loop via
    `--iterationCount` -> TOOL_MANAGED, command is the wrap argv."""
    inner = DriverCommand(argv=["maestro", "test", "prestamos-warm"], automated=True)
    wrap = SamplerCommand(
        argv=["flashlight", "test", "--testCommand", "maestro test prestamos-warm", "--iterationCount", "10"],
        results_path="/tmp/results/prestamos-warm.json",
        manages_iterations=True,
    )
    capture = CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"])

    plan = compose_execution_plan(inner, iterations=10, wrap=wrap, capture=capture)

    assert plan.loop_mode is LoopMode.TOOL_MANAGED
    assert plan.command == wrap.argv
    assert plan.inner is inner
    assert plan.iterations == 10
    assert plan.capture is capture
    assert plan.results_path == wrap.results_path


def test_execution_plan_maestro_no_flashlight_is_driver_managed():
    """Shape (b): no sampler wraps the command -> the driver itself loops N
    times over the inner Maestro command."""
    inner = DriverCommand(argv=["maestro", "test", "prestamos-warm"], automated=True)

    plan = compose_execution_plan(inner, iterations=5, wrap=None, capture=None)

    assert plan.loop_mode is LoopMode.DRIVER_MANAGED
    assert plan.command == inner.argv
    assert plan.results_path is None
    assert plan.capture is None


def test_execution_plan_manual_driver_with_markers_is_driver_managed_no_command():
    """Shape (c): ManualDriver has no automated command; the driver prompts
    the user N times while logcat captures markers."""
    inner = DriverCommand(argv=None, automated=False, prompt="Run the flow manually, then confirm.")
    capture = CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"])

    plan = compose_execution_plan(inner, iterations=8, wrap=None, capture=capture)

    assert plan.loop_mode is LoopMode.DRIVER_MANAGED
    assert plan.command is None
    assert plan.capture is capture


def test_execution_plan_markers_only_is_driver_managed_no_command():
    """Shape (d): markers-only — same composition as manual+markers, no
    Flashlight, no automated command."""
    inner = DriverCommand(argv=None, automated=False, prompt="Run the flow manually, then confirm.")
    capture = CaptureSpec(argv=["adb", "logcat", "-s", "ReactNativeJS:V"])

    plan = compose_execution_plan(inner, iterations=1, wrap=None, capture=capture)

    assert plan.loop_mode is LoopMode.DRIVER_MANAGED
    assert plan.command is None
    assert plan.iterations == 1


def test_execution_plan_wrap_without_manages_iterations_is_driver_managed():
    """A sampler that wraps the command but does NOT own the loop (e.g. a
    future single-shot `measure` seam) must NOT flip to TOOL_MANAGED."""
    inner = DriverCommand(argv=["maestro", "test", "prestamos-warm"], automated=True)
    wrap = SamplerCommand(
        argv=["flashlight", "measure", "--testCommand", "maestro test prestamos-warm"],
        results_path="/tmp/results/prestamos-warm.json",
        manages_iterations=False,
    )

    plan = compose_execution_plan(inner, iterations=3, wrap=wrap, capture=None)

    assert plan.loop_mode is LoopMode.DRIVER_MANAGED
    assert plan.command == wrap.argv
    assert plan.results_path == wrap.results_path
