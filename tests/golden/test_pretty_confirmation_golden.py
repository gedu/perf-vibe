"""Golden tests for the pretty confirmation reporter (SKILL rule 8:
"Golden files for pretty output with color forced off (`--update-golden`
regenerates)."). Also golds the ASCII banner and proves it never leaks
into a data stream.
"""

from __future__ import annotations

from pathlib import Path

from perf.application.run_flow import RunFlowResult
from perf.cli.banner import render_banner
from perf.cli.output.json_reporter import render_json
from perf.cli.output.pretty import render_confirmation
from perf.contracts.json_v1 import build_run_payload
from perf.domain.model import Marker, SystemSample

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _fixed_result() -> RunFlowResult:
    return RunFlowResult(
        run_id=7,
        flow_name="checkout-warm",
        device_key="Pixel 8 Pro|Android 14|physical",
        git_commit="deadbeef",
        is_dev_bundle=False,
        source="local:eduardo",
        mode="warm",
        iterations=2,
        markers=(
            Marker(name="checkout", value=900.0, unit="ms"),
            Marker(name="checkout", value=950.0, unit="ms"),
        ),
        samples=(
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
            SystemSample(
                iteration_idx=1,
                total_time_ms=1180.0,
                start_time_ms=290.0,
                fps_avg=59.0,
                fps_min=45.0,
                ram_avg_mb=510.0,
                ram_peak_mb=590.0,
                cpu_avg_pct=28.0,
                cpu_peak_pct=48.0,
            ),
        ),
        raw_report_path="results/checkout-warm.json",
        partial_coverage=False,
    )


def _assert_or_update_golden(request, fixture_name: str, actual: str) -> None:
    fixture_path = _FIXTURES_DIR / fixture_name
    if request.config.getoption("--update-golden"):
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(actual)
        return
    expected = fixture_path.read_text()
    assert actual == expected, (
        f"golden mismatch for {fixture_name} — run with --update-golden to "
        "regenerate if this change is intentional"
    )


def test_pretty_confirmation_matches_golden_with_color_forced_off(request):
    actual = render_confirmation(_fixed_result(), color=False)
    _assert_or_update_golden(request, "run_confirmation.txt", actual)


def test_banner_matches_golden_with_color_forced_off(request):
    actual = render_banner(color=False)
    _assert_or_update_golden(request, "banner.txt", actual)


def test_banner_never_appears_in_json_payload():
    payload = build_run_payload(_fixed_result())
    json_text = render_json(payload)
    banner_plain = render_banner(color=False)
    for line in banner_plain.splitlines():
        if line.strip():
            assert line not in json_text, "banner text leaked into the --json data stream"
