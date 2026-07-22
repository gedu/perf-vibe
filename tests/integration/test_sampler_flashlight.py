"""Integration tests for `FlashlightSampler` (design §3, discovery #37).

RED-before-GREEN: written before `src/perf/adapters/sampler_flashlight.py`
existed. Fixture-driven (`tests/fixtures/flashlight_sample.json`) — never
touches a live device or the real Flashlight binary. Asserts the per-
iteration aggregation math (fps avg/min, ram avg/peak, cpu avg/peak = sum
of `perName` per sample), that `total_time_ms`/`start_time_ms` come from
the iteration itself even with empty `measures[]`, and — the hard boundary
— that no network field is ever read (SKILL rule 9 / spec: "never ingest
network metrics").
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from perf.adapters.sampler_flashlight import FlashlightParseError, FlashlightSampler
from perf.domain.model import DriverCommand

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def test_parse_aggregates_per_iteration_from_fixture():
    sampler = FlashlightSampler()
    result = sampler.parse(str(_FIXTURES_DIR / "flashlight_sample.json"))
    samples = result.samples

    assert len(samples) == 2
    assert result.partial_coverage is False

    first, second = samples
    assert first.iteration_idx == 0
    assert first.total_time_ms == 46712
    assert first.start_time_ms == 1342
    assert first.fps_avg == pytest.approx((59.28 + 55.0) / 2)
    assert first.fps_min == 55.0
    assert first.ram_avg_mb == pytest.approx((210.5 + 240.0) / 2)
    assert first.ram_peak_mb == 240.0
    cpu_sample_1 = 11.9 + 29.8 + 5.0
    cpu_sample_2 = 8.0 + 20.0 + 4.0
    assert first.cpu_avg_pct == pytest.approx((cpu_sample_1 + cpu_sample_2) / 2)
    assert first.cpu_peak_pct == pytest.approx(cpu_sample_1)

    assert second.iteration_idx == 1
    assert second.total_time_ms == 45000
    assert second.start_time_ms == 1200
    assert second.fps_avg == 58.0
    assert second.fps_min == 58.0


def test_parse_never_reads_network_field_even_when_present():
    """The fixture deliberately contains a `network` key inside `measures[]`
    to prove this boundary — `SystemSample` structurally has no network
    field and the parser never references the key, so nothing
    network-related can ever reach the store."""
    sampler = FlashlightSampler()
    samples = sampler.parse(str(_FIXTURES_DIR / "flashlight_sample.json")).samples

    field_names = {f.name for f in fields(samples[0])}
    assert not any("network" in name for name in field_names)


def test_parse_empty_measures_still_records_iteration_time(tmp_path):
    report = {
        "name": "Results",
        "status": "SUCCESS",
        "iterations": [
            {"time": 1000.0, "startTime": 200.0, "status": "SUCCESS", "measures": []}
        ],
    }
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(report))

    sampler = FlashlightSampler()
    result = sampler.parse(str(path))
    samples = result.samples

    assert len(samples) == 1
    assert result.partial_coverage is False
    sample = samples[0]
    assert sample.total_time_ms == 1000.0
    assert sample.start_time_ms == 200.0
    assert sample.fps_avg is None
    assert sample.fps_min is None
    assert sample.ram_avg_mb is None
    assert sample.ram_peak_mb is None
    assert sample.cpu_avg_pct is None
    assert sample.cpu_peak_pct is None


def test_wrap_builds_flashlight_argv_wrapping_the_inner_maestro_command(tmp_path):
    sampler = FlashlightSampler()
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    results_path = tmp_path / "run1.json"

    wrapped = sampler.wrap(inner, iterations=5, restart=False, results_path=str(results_path))

    assert isinstance(wrapped.argv, list)
    assert wrapped.argv[0] == "flashlight"
    assert "--testCommand" in wrapped.argv
    test_command_idx = wrapped.argv.index("--testCommand") + 1
    assert wrapped.argv[test_command_idx] == "maestro test flows/checkout.yaml"
    assert "--iterationCount" in wrapped.argv
    assert "5" in wrapped.argv
    assert "--resultsFilePath" in wrapped.argv
    assert str(results_path) in wrapped.argv
    assert "--skipRestart" in wrapped.argv  # warm (restart=False) -> --skipRestart present
    assert wrapped.manages_iterations is True


def test_wrap_omits_skip_restart_flag_when_restart_forces_cold(tmp_path):
    sampler = FlashlightSampler()
    inner = DriverCommand(argv=["maestro", "test", "flows/checkout.yaml"], automated=True)
    wrapped = sampler.wrap(inner, iterations=1, restart=True, results_path=str(tmp_path / "r.json"))
    assert "--skipRestart" not in wrapped.argv


def test_wrap_returns_none_for_manual_driver_with_no_automated_command(tmp_path):
    """Manual + Flashlight `measure` is a documented, not-built-in-Phase-1
    seam (design §3/§7) — `wrap()` returns `None` when `inner.argv is None`."""
    sampler = FlashlightSampler()
    inner = DriverCommand(argv=None, automated=False, prompt="do the thing")
    wrapped = sampler.wrap(inner, iterations=1, restart=False, results_path=str(tmp_path / "r.json"))
    assert wrapped is None


def test_top_level_failure_status_is_never_silently_aggregated(tmp_path):
    """Fix (CRITICAL resilience review): `parse()` never read `status`, so a
    FAILURE/timed-out run got aggregated and persisted as if successful,
    poisoning the regression history. A non-SUCCESS top-level status must
    surface as a clear parse error, never normal-looking samples."""
    report = {
        "name": "Results",
        "status": "FAILURE",
        "iterations": [
            {
                "time": 46712,
                "startTime": 1342,
                "status": "SUCCESS",
                "measures": [{"fps": 59.28, "ram": 210.5, "cpu": {"perName": {"UI Thread": 11.9}}}],
            }
        ],
    }
    path = tmp_path / "failed.json"
    path.write_text(json.dumps(report))

    sampler = FlashlightSampler()
    with pytest.raises(FlashlightParseError):
        sampler.parse(str(path))


def test_one_failed_iteration_among_successes_yields_only_success_sample_and_partial_coverage(tmp_path):
    """Fix (CRITICAL resilience review): a FAILURE iteration must be
    excluded from aggregation (never blended into a normal SystemSample)
    and surfaced via partial coverage rather than silently vanishing."""
    report = {
        "name": "Results",
        "status": "SUCCESS",
        "iterations": [
            {
                "time": 46712,
                "startTime": 1342,
                "status": "SUCCESS",
                "measures": [{"fps": 59.28, "ram": 210.5, "cpu": {"perName": {"UI Thread": 11.9}}}],
            },
            {
                "time": 20000,
                "startTime": 900,
                "status": "FAILURE",
                "measures": [{"fps": 10.0, "ram": 900.0, "cpu": {"perName": {"UI Thread": 99.0}}}],
            },
        ],
    }
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(report))

    sampler = FlashlightSampler()
    result = sampler.parse(str(path))

    assert len(result.samples) == 1
    assert result.samples[0].iteration_idx == 0
    assert result.samples[0].fps_avg == 59.28
    assert result.partial_coverage is True
