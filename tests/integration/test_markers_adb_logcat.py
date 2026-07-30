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

import pytest

from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource
from perf.domain.model import Marker, MarkerParseResult

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


# ===== marker diagnostics (why zero/partial coverage) =====


def test_diagnostic_is_none_when_coverage_is_full():
    source = AdbLogcatMarkerSource()
    result = source.parse(_load_lines(), iterations=2)  # 2 completed >= 2 iterations
    assert result.diagnostic is None  # full coverage -> nothing to explain


def test_diagnostic_explains_no_logcat_output_at_all():
    source = AdbLogcatMarkerSource()
    result = source.parse([], iterations=3)
    assert result.diagnostic is not None
    assert "no logcat output" in result.diagnostic.lower()


def test_diagnostic_explains_lines_but_no_perf_markers():
    source = AdbLogcatMarkerSource()
    lines = ["--------- beginning of main", "some app log line", "another line"]
    result = source.parse(lines, iterations=3)
    assert result.markers == ()
    assert result.diagnostic is not None
    assert "[PERF]" in result.diagnostic
    assert "3" in result.diagnostic  # reports the 3 captured-but-unmatched lines


def test_diagnostic_explains_perf_lines_but_partial_coverage():
    source = AdbLogcatMarkerSource()
    result = source.parse(["[PERF] checkout: 900ms"], iterations=3)  # 1 completed, 3 expected
    assert len(result.markers) == 1
    assert result.diagnostic is not None
    assert "1 of 3" in result.diagnostic
    assert "markEnd" in result.diagnostic  # points at the likely cause


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


@pytest.mark.parametrize(
    "payload_line",
    [
        pytest.param("[PERF] [1, 2]", id="json-non-dict"),
        pytest.param('[PERF] {"name": "x"}', id="json-missing-value"),
        pytest.param('[PERF] {"value": 1}', id="json-missing-name"),
        pytest.param('[PERF] {"name": "x", "value": "fast"}', id="json-non-numeric-value"),
        pytest.param('[PERF] {"name": "x", "value": NaN}', id="json-nan-value"),
        pytest.param('[PERF] {"name": "x", "value": Infinity}', id="json-inf-value"),
        pytest.param('[PERF] {"name": "x", "value": -Infinity}', id="json-neg-inf-value"),
        pytest.param('[PERF] {"name": "x", "value": -12.5}', id="json-negative-value"),
    ],
)
def test_malformed_or_nonsense_json_values_are_skipped_never_persisted(payload_line):
    """The full malformed-JSON matrix — every case skips, none crashes, none
    emits a Marker. The non-finite cases matter most: Python's `json.loads`
    ACCEPTS `NaN`/`Infinity`, and downstream a NaN binds as NULL into the
    `NOT NULL duration_ms` column — one bad line would roll back the ENTIRE
    N-iteration run at ingestion. Negative durations are clock-skew garbage
    the text-form regex already rejects; the JSON path must agree."""
    source = AdbLogcatMarkerSource()
    result = source.parse([payload_line], iterations=1)
    assert result.markers == ()
    assert result.partial_coverage is True


def test_empty_perf_payload_is_skipped():
    source = AdbLogcatMarkerSource()
    result = source.parse(["[PERF]", "[PERF]   "], iterations=1)
    assert result.markers == ()


def test_oversized_line_is_skipped_not_parsed():
    """SKILL rule 5: bound line length — a pathologically long line must
    never reach the regex/JSON parser."""
    source = AdbLogcatMarkerSource()
    huge_line = "[PERF] checkout: " + ("9" * 20000) + "ms"
    result = source.parse([huge_line], iterations=1)
    assert result.markers == ()


# ===== characterization: parse()'s output must survive the classify_line
# extraction byte-identical (markers-command design "Modified Capabilities":
# parse()'s signature and observable MarkerParseResult stay UNCHANGED). This
# test MUST pass against the OLD parse() BEFORE the refactor, and MUST still
# pass unchanged AFTER parse() delegates to classify_line. =====


@pytest.mark.parametrize(
    ("lines", "iterations", "expected"),
    [
        pytest.param(
            ["[PERF] checkout: 900ms"],
            1,
            MarkerParseResult(
                markers=(Marker(name="checkout", value=900.0, unit="ms"),),
                partial_coverage=False,
                diagnostic=None,
            ),
            id="completed-text-marker",
        ),
        pytest.param(
            ['[PERF] {"name":"login","value":450,"unit":"ms"}'],
            1,
            MarkerParseResult(
                markers=(Marker(name="login", value=450.0, unit="ms"),),
                partial_coverage=False,
                diagnostic=None,
            ),
            id="completed-json-marker",
        ),
        pytest.param(
            ["[PERF] markStart:onboarding"],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "saw 1 `[PERF]` line(s) but only 0 of 1 iteration(s) produced a "
                    "COMPLETED marker — a `markStart` without a matching `markEnd` is "
                    "skipped (a crash or early exit mid-flow?)."
                ),
            ),
            id="bare-mark-start",
        ),
        pytest.param(
            ['[PERF-META] {"app_version":"4.20.1"}'],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "captured 1 logcat line(s) but NONE carried a `[PERF]` marker "
                    "(tag filter `ReactNativeJS:V`) — the app may not be emitting "
                    "`[PERF]` markers, or they are logged under a different tag."
                ),
            ),
            id="perf-meta-line",
        ),
        pytest.param(
            ["[PERF] not-a-number: abcms"],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "saw 1 `[PERF]` line(s) but only 0 of 1 iteration(s) produced a "
                    "COMPLETED marker — a `markStart` without a matching `markEnd` is "
                    "skipped (a crash or early exit mid-flow?)."
                ),
            ),
            id="malformed-text",
        ),
        pytest.param(
            ["[PERF] {not valid json"],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "saw 1 `[PERF]` line(s) but only 0 of 1 iteration(s) produced a "
                    "COMPLETED marker — a `markStart` without a matching `markEnd` is "
                    "skipped (a crash or early exit mid-flow?)."
                ),
            ),
            id="invalid-json",
        ),
        pytest.param(
            ['[PERF] {"name": "x", "value": NaN}'],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "saw 1 `[PERF]` line(s) but only 0 of 1 iteration(s) produced a "
                    "COMPLETED marker — a `markStart` without a matching `markEnd` is "
                    "skipped (a crash or early exit mid-flow?)."
                ),
            ),
            id="nan-value",
        ),
        pytest.param(
            ['[PERF] {"name": "x", "value": -Infinity}'],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "saw 1 `[PERF]` line(s) but only 0 of 1 iteration(s) produced a "
                    "COMPLETED marker — a `markStart` without a matching `markEnd` is "
                    "skipped (a crash or early exit mid-flow?)."
                ),
            ),
            id="negative-infinity-value",
        ),
        pytest.param(
            ['[PERF] {"name": "x", "value": -12.5}'],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "saw 1 `[PERF]` line(s) but only 0 of 1 iteration(s) produced a "
                    "COMPLETED marker — a `markStart` without a matching `markEnd` is "
                    "skipped (a crash or early exit mid-flow?)."
                ),
            ),
            id="negative-value",
        ),
        pytest.param(
            ["[PERF] checkout: " + ("9" * 20000) + "ms"],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "captured 1 logcat line(s) but NONE carried a `[PERF]` marker "
                    "(tag filter `ReactNativeJS:V`) — the app may not be emitting "
                    "`[PERF]` markers, or they are logged under a different tag."
                ),
            ),
            id="oversized-line",
        ),
        pytest.param(
            ["garbage line without perf marker"],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "captured 1 logcat line(s) but NONE carried a `[PERF]` marker "
                    "(tag filter `ReactNativeJS:V`) — the app may not be emitting "
                    "`[PERF]` markers, or they are logged under a different tag."
                ),
            ),
            id="non-perf-line",
        ),
        pytest.param(
            [],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                diagnostic=(
                    "no logcat output was captured at all — check the device is connected "
                    "(`adb devices`) and streaming logs, and that the flow actually ran."
                ),
            ),
            id="empty-input",
        ),
        pytest.param(
            ["[PERF]   "],
            1,
            MarkerParseResult(
                markers=(),
                partial_coverage=True,
                # An empty/whitespace-only payload after the `[PERF]` tag is
                # skipped BEFORE `perf_lines_seen` is incremented — so the
                # diagnostic is the "NONE carried a `[PERF]` marker" form, NOT
                # the "saw N `[PERF]` line(s)" form. Pinning this guards the
                # drift W1: a future change that counted the empty line would
                # silently flip this diagnostic and every other test would
                # still pass.
                diagnostic=(
                    "captured 1 logcat line(s) but NONE carried a `[PERF]` marker "
                    "(tag filter `ReactNativeJS:V`) — the app may not be emitting "
                    "`[PERF]` markers, or they are logged under a different tag."
                ),
            ),
            id="empty-perf-payload",
        ),
    ],
)
def test_characterization_parse_output_is_pinned(lines, iterations, expected):
    """Golden pin of `parse()`'s FULL `MarkerParseResult` (markers tuple,
    partial_coverage, diagnostic) for representative inputs — MUST pass
    against the current `parse()` before the `classify_line` extraction and
    MUST still pass, byte-identical, after `parse()` delegates to it."""
    source = AdbLogcatMarkerSource()
    result = source.parse(lines, iterations=iterations)
    assert result == expected
