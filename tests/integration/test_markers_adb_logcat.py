"""Integration tests for `AdbLogcatMarkerSource` (design §4).

RED-before-GREEN: written before `src/perf/adapters/markers_adb_logcat.py`
existed. Fixture-driven (`tests/fixtures/logcat_sample.txt`) — both marker
forms, a malformed-JSON line (must be skipped via `json.loads`, never
`eval`), a `markStart`-without-`markEnd` case (skipped + partial coverage
surfaced), and a `[PERF-META]` line that markers must ignore (context
only, consumed by `RunContextProvider` instead).
"""

from __future__ import annotations

from pathlib import Path

from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "logcat_sample.txt"


def _load_lines() -> list[str]:
    return _FIXTURE.read_text().splitlines()


def test_parse_captures_both_marker_forms():
    source = AdbLogcatMarkerSource()
    result = source.parse(_load_lines(), iterations=3)

    by_name = {m.name: m for m in result.markers}
    assert by_name["checkout"].value == 900.0
    assert by_name["checkout"].unit == "ms"
    assert by_name["login"].value == 450.0
    assert by_name["login"].unit == "ms"


def test_markstart_without_markend_is_skipped_and_flags_partial_coverage():
    source = AdbLogcatMarkerSource()
    result = source.parse(_load_lines(), iterations=3)

    names = {m.name for m in result.markers}
    assert "onboarding" not in names  # markStart never completed -> no marker emitted
    assert len(result.markers) == 2  # only checkout + login completed
    assert result.partial_coverage is True  # 2 completed occurrences < 3 iterations


def test_full_coverage_when_occurrences_match_iterations():
    source = AdbLogcatMarkerSource()
    result = source.parse(_load_lines(), iterations=2)
    assert result.partial_coverage is False


def test_malformed_json_marker_line_is_skipped_not_crashed():
    source = AdbLogcatMarkerSource()
    result = source.parse(["[PERF] {not valid json"], iterations=1)
    assert result.markers == ()
    assert result.partial_coverage is True


def test_arbitrary_metric_names_no_hardcoded_route():
    source = AdbLogcatMarkerSource()
    result = source.parse(["[PERF] some_totally_arbitrary_metric_name: 42ms"], iterations=1)
    assert result.markers[0].name == "some_totally_arbitrary_metric_name"


def test_perf_meta_line_is_ignored_by_marker_parsing():
    source = AdbLogcatMarkerSource()
    result = source.parse(_load_lines(), iterations=3)
    names = {m.name for m in result.markers}
    assert "app_version" not in names
    assert "is_dev_bundle" not in names


def test_capture_spec_returns_adb_logcat_argv_list():
    source = AdbLogcatMarkerSource()
    spec = source.capture_spec()
    assert isinstance(spec.argv, list)
    assert spec.argv[:2] == ["adb", "logcat"]


def test_capture_spec_pins_device_serial_when_device_configured():
    """Fix (resilience review): on a host with 2+ devices, an unpinned
    `adb logcat` dies with 'more than one device' and silently yields zero
    markers — device pinning must mirror MaestroDriver/BashRunContextProvider."""
    source = AdbLogcatMarkerSource(device="emulator-5554")
    spec = source.capture_spec()
    assert spec.argv == ["adb", "-s", "emulator-5554", "logcat", "-s", "ReactNativeJS:V"]


def test_capture_spec_omits_device_flag_when_no_device_configured():
    source = AdbLogcatMarkerSource()
    spec = source.capture_spec()
    assert spec.argv == ["adb", "logcat", "-s", "ReactNativeJS:V"]


def test_oversized_line_is_skipped_not_parsed():
    """SKILL rule 5: bound line length — a pathologically long line must
    never reach the regex/JSON parser."""
    source = AdbLogcatMarkerSource()
    huge_line = "[PERF] checkout: " + ("9" * 20000) + "ms"
    result = source.parse([huge_line], iterations=1)
    assert result.markers == ()
