"""Pure-domain unit tests for `perf.domain.model` (task 2.4 GREEN).

No I/O, no adapters — only dataclass construction and immutability.
"""

from __future__ import annotations

import dataclasses

import pytest

from perf.domain.model import (
    Device,
    Flow,
    Marker,
    Measure,
    Metric,
    Run,
    RunContext,
    SystemSample,
    Verdict,
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


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Device(device_key="Pixel 8 Pro|Android 14|physical", model="Pixel 8 Pro", os_version="Android 14"),
        lambda: Flow(name="prestamos-warm"),
        lambda: Metric(name="/loans/details/:id"),
        lambda: Marker(metric_name="/loans/details/:id", duration_ms=900.0),
        lambda: SystemSample(iteration_idx=0, fps_avg=59.8, cpu_pct_avg=12.4, ram_mb_avg=210.5),
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


def test_metric_defaults_unit_ms():
    metric = Metric(name="/loans/details/:id")
    assert metric.unit == "ms"


def test_run_run_id_defaults_to_none_before_persistence():
    run = Run(
        flow_name="prestamos-warm",
        device_key="Pixel 8 Pro|Android 14|physical",
        started_at="2026-07-22T00:00:00Z",
        iterations=10,
        mode="warm",
        context=_run_context(),
    )
    assert run.run_id is None


def test_run_context_is_dev_bundle_originates_only_from_perf_meta():
    ctx = _run_context(is_dev_bundle=None)
    assert ctx.is_dev_bundle is None  # unknown/null, never guessed


def test_marker_holds_stable_template_name_not_raw_path():
    marker = Marker(metric_name="/loans/details/:id", duration_ms=123.4)
    assert ":id" in marker.metric_name


def test_system_sample_has_no_network_fields():
    sample = SystemSample(iteration_idx=1, fps_avg=60.0, cpu_pct_avg=10.0, ram_mb_avg=180.0)
    field_names = {f.name for f in dataclasses.fields(sample)}
    assert not any("net" in name for name in field_names)
