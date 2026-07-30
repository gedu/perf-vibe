"""Unit tests for `classify_line` (markers-command design, "Shared
Line-Classification Function"): the SOLE owner of tag/regex/JSON-detection
logic that both `AdbLogcatMarkerSource.parse()` and a future `markers
doctor` classify through. Each `LineKind`/failure `reason` is exercised
directly here, independent of `parse()`'s aggregation."""

from __future__ import annotations

import pytest

from perf.adapters.markers_adb_logcat import (
    REASON_INVALID_JSON,
    REASON_INVALID_VALUE,
    REASON_MALFORMED_TEXT,
    REASON_OVERSIZED,
    LineKind,
    LineVerdict,
    classify_line,
)
from perf.domain.model import Marker

# ===== COMPLETED =====


def test_classify_text_marker_is_completed():
    verdict = classify_line("[PERF] checkout: 900ms")
    assert verdict == LineVerdict(
        kind=LineKind.COMPLETED, marker=Marker(name="checkout", value=900.0, unit="ms")
    )


def test_classify_json_marker_is_completed():
    verdict = classify_line('[PERF] {"name":"login","value":450,"unit":"ms"}')
    assert verdict == LineVerdict(
        kind=LineKind.COMPLETED, marker=Marker(name="login", value=450.0, unit="ms")
    )


def test_classify_text_marker_defaults_unit_to_ms():
    verdict = classify_line("[PERF] cold_start: 12")
    assert verdict.kind is LineKind.COMPLETED
    assert verdict.marker is not None
    assert verdict.marker.unit == "ms"


# ===== MARK_START =====


def test_classify_bare_mark_start_is_mark_start_with_no_marker():
    verdict = classify_line("[PERF] markStart:onboarding")
    assert verdict == LineVerdict(kind=LineKind.MARK_START)


# ===== PERF_META =====


def test_classify_perf_meta_line_is_perf_meta_with_no_marker():
    verdict = classify_line('[PERF-META] {"app_version":"4.20.1"}')
    assert verdict == LineVerdict(kind=LineKind.PERF_META)


# ===== IGNORED =====


def test_classify_non_perf_line_is_ignored():
    verdict = classify_line("garbage line without perf marker")
    assert verdict == LineVerdict(kind=LineKind.IGNORED)


def test_classify_empty_perf_payload_is_ignored():
    verdict = classify_line("[PERF]   ")
    assert verdict == LineVerdict(kind=LineKind.IGNORED)


# ===== FAILURE: malformed text =====


@pytest.mark.parametrize(
    "payload_line",
    [
        pytest.param("[PERF] not-a-number: abcms", id="non-numeric-value"),
        # Does NOT start with "{", so it never enters JSON classification —
        # it fails the text-form regex instead (no numeric value after ":").
        pytest.param("[PERF] [1, 2]", id="no-colon-not-json-shaped"),
    ],
)
def test_classify_non_numeric_text_value_is_malformed_text_failure(payload_line):
    verdict = classify_line(payload_line)
    assert verdict == LineVerdict(kind=LineKind.FAILURE, reason=REASON_MALFORMED_TEXT)


# ===== FAILURE: invalid JSON =====


@pytest.mark.parametrize(
    "payload_line",
    [
        pytest.param("[PERF] {not valid json", id="unparsable"),
        pytest.param('[PERF] {"name": "x"}', id="missing-value"),
        pytest.param('[PERF] {"value": 1}', id="missing-name"),
        pytest.param('[PERF] {"name": "x", "value": "fast"}', id="non-numeric-value"),
    ],
)
def test_classify_invalid_json_shapes_are_invalid_json_failure(payload_line):
    verdict = classify_line(payload_line)
    assert verdict == LineVerdict(kind=LineKind.FAILURE, reason=REASON_INVALID_JSON)


# ===== FAILURE: invalid value (non-finite / negative) =====


@pytest.mark.parametrize(
    "payload_line",
    [
        pytest.param('[PERF] {"name": "x", "value": NaN}', id="nan"),
        pytest.param('[PERF] {"name": "x", "value": Infinity}', id="positive-infinity"),
        pytest.param('[PERF] {"name": "x", "value": -Infinity}', id="negative-infinity"),
        pytest.param('[PERF] {"name": "x", "value": -12.5}', id="negative"),
    ],
)
def test_classify_non_finite_or_negative_json_value_is_invalid_value_failure(payload_line):
    verdict = classify_line(payload_line)
    assert verdict == LineVerdict(kind=LineKind.FAILURE, reason=REASON_INVALID_VALUE)


# ===== FAILURE: oversized =====


def test_classify_oversized_line_is_oversized_failure():
    """SKILL rule 5: bound line length before any regex/JSON parsing —
    `classify_line` itself owns this bound, never reason-attributed beyond
    `oversized` (never `malformed_text`/`invalid_json`, since the payload is
    never even inspected)."""
    huge_line = "[PERF] checkout: " + ("9" * 20000) + "ms"
    verdict = classify_line(huge_line)
    assert verdict == LineVerdict(kind=LineKind.FAILURE, reason=REASON_OVERSIZED)
