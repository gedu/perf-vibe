"""`FlowDriver` port adapter tests — `ReplayDriver` (device-free demo/testing
seam). RED-before-GREEN: written before `src/perf/adapters/driver_replay.py`
existed.

Proves: `command()` returns a non-`None` `argv` (so a configured
`FlashlightSampler.wrap()` actually wraps and produces a `results_path` —
otherwise no samples would ever get replayed), `drive()` copies the
Flashlight fixture to `plan.results_path` and returns the logcat fixture's
lines verbatim, and markers-only replay (no Flashlight fixture) still works.
"""

from __future__ import annotations

import json
from pathlib import Path

from perf.adapters.driver_replay import ReplayDriver
from perf.adapters.sampler_flashlight import FlashlightSampler
from perf.domain.model import CaptureSpec, ExecutionPlan, LoopMode


def _write_logcat(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "some noise line",
                "ReactNativeJS: [PERF] checkout: 900ms",
                'ReactNativeJS: [PERF] {"name": "ttfp", "value": 450, "unit": "ms"}',
            ]
        )
    )
    return path


def _write_flashlight(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "name": "Results",
                "status": "SUCCESS",
                "iterations": [
                    {
                        "time": 1000,
                        "startTime": 0,
                        "status": "SUCCESS",
                        "measures": [{"fps": 59.0, "ram": 200.0, "cpu": {"perName": {"UI": 10.0}}}],
                    }
                ],
            }
        )
    )
    return path


def test_command_returns_non_none_argv_so_flashlight_wraps(tmp_path: Path):
    logcat = _write_logcat(tmp_path / "logcat.txt")
    driver = ReplayDriver(logcat_path=str(logcat))

    inner = driver.command("demo", mode="warm", restart=False, env=None)

    assert inner.argv is not None
    assert inner.automated is True

    # Confirm the CRITICAL contract: a configured FlashlightSampler.wrap()
    # actually wraps (returns non-None) given this DriverCommand — proving
    # samples would get replayed rather than silently skipped.
    wrap = FlashlightSampler().wrap(
        inner, iterations=2, restart=False, results_path=str(tmp_path / "out.json")
    )
    assert wrap is not None
    assert wrap.results_path == str(tmp_path / "out.json")


def test_drive_copies_flashlight_fixture_and_returns_logcat_lines(tmp_path: Path):
    logcat = _write_logcat(tmp_path / "logcat.txt")
    flashlight = _write_flashlight(tmp_path / "flashlight.json")
    results_path = tmp_path / "results" / "demo-warm-run.json"

    driver = ReplayDriver(logcat_path=str(logcat), flashlight_path=str(flashlight))
    inner = driver.command("demo", mode="warm", restart=False, env=None)
    plan = ExecutionPlan(
        command=inner.argv,
        inner=inner,
        loop_mode=LoopMode.TOOL_MANAGED,
        iterations=2,
        capture=CaptureSpec(argv=["adb", "logcat"]),
        results_path=str(results_path),
    )

    result = driver.drive(plan)

    assert result.ok is True
    assert result.iteration_outcomes == ("ok", "ok")
    assert result.capture_failed is False
    assert results_path.is_file()
    assert json.loads(results_path.read_text()) == json.loads(flashlight.read_text())
    assert result.logcat_lines == tuple(logcat.read_text().splitlines())


def test_drive_markers_only_skips_copy_when_no_flashlight_fixture(tmp_path: Path):
    logcat = _write_logcat(tmp_path / "logcat.txt")
    driver = ReplayDriver(logcat_path=str(logcat))
    inner = driver.command("demo", mode="warm", restart=False, env=None)
    plan = ExecutionPlan(
        command=inner.argv,
        inner=inner,
        loop_mode=LoopMode.DRIVER_MANAGED,
        iterations=1,
        capture=CaptureSpec(argv=["adb", "logcat"]),
        results_path=None,
    )

    result = driver.drive(plan)

    assert result.ok is True
    assert result.iteration_outcomes == ("ok",)
    assert result.logcat_lines == tuple(logcat.read_text().splitlines())
