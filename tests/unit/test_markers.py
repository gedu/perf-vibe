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
from perf.cli.commands.markers import (
    AmbiguousDoctorInputError,
    bucket_lines,
    detect_mode,
    emitted_sample,
    render_snippet,
)
from perf.domain.model import PERF_TAG, Marker

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


# ===== Phase 3: `cli/commands/markers.py` pure helpers =====

# ----- render_snippet / emitted_sample (spec "Text-Form Emitter Contract") -----


def test_render_snippet_ts_includes_trio_markers_map_and_type_annotations():
    code = render_snippet("ts")
    assert "markStart" in code
    assert "markEnd" in code
    assert "measureMark" in code
    assert "MARKERS" in code
    assert ": string" in code  # TS-only type annotation
    assert PERF_TAG in code
    assert "__PERF_TAG__" not in code  # placeholder must always be substituted


def test_render_snippet_js_includes_trio_and_markers_map_without_ts_annotations():
    code = render_snippet("js")
    assert "markStart" in code
    assert "markEnd" in code
    assert "measureMark" in code
    assert "MARKERS" in code
    assert ": string" not in code
    assert PERF_TAG in code
    assert "__PERF_TAG__" not in code


def test_emitted_sample_is_one_representative_perf_tag_line():
    assert emitted_sample() == f"{PERF_TAG} example: 123ms"


# ----- render_snippet fidelity vs the user's proven-working module (C-1) -----


def test_render_snippet_ts_mirrors_the_reference_module_faithfully():
    code = render_snippet("ts")
    assert "import performance from 'react-native-performance';" in code
    assert "import { performance }" not in code  # never the named-import variant
    assert "try {" in code
    assert "console.warn(`Performance measure failed for ${name}:`, error);" in code
    assert "measureEntry.duration" in code
    assert "MARKERS" in code


def test_render_snippet_js_mirrors_the_reference_module_faithfully():
    code = render_snippet("js")
    assert "import performance from 'react-native-performance';" in code
    assert "import { performance }" not in code
    assert "try {" in code
    assert "console.warn(`Performance measure failed for ${name}:`, error);" in code
    assert "measureEntry.duration" in code
    assert "MARKERS" in code


# ----- detect_mode (spec "Doctor Input Mode Detection") -----


def test_detect_mode_is_line_when_arg_present_and_stdin_is_a_tty():
    assert detect_mode("[PERF] x: 1ms", stdin_is_tty=True) == "line"


def test_detect_mode_is_stdin_when_no_arg_and_stdin_is_not_a_tty():
    assert detect_mode(None, stdin_is_tty=False) == "stdin"


def test_detect_mode_raises_when_both_arg_and_piped_stdin():
    with pytest.raises(AmbiguousDoctorInputError):
        detect_mode("[PERF] x: 1ms", stdin_is_tty=False)


def test_detect_mode_raises_when_neither_arg_nor_piped_stdin():
    with pytest.raises(AmbiguousDoctorInputError):
        detect_mode(None, stdin_is_tty=True)


# ----- bucket_lines (spec "Diagnosis Categories") -----


def test_bucket_lines_categorizes_each_kind_in_one_pass():
    lines = [
        "[PERF] checkout: 900ms",
        "[PERF] markStart:onboarding",
        '[PERF-META] {"app_version":"4.20.1"}',
        "[PERF] not-a-number: abcms",
        "unrelated log line",
    ]
    breakdown = bucket_lines(lines)
    assert breakdown.parsed == (Marker(name="checkout", value=900.0, unit="ms"),)
    assert breakdown.mark_start_without_end == 1
    assert breakdown.perf_meta == 1
    assert breakdown.parse_failures == (("[PERF] not-a-number: abcms", REASON_MALFORMED_TEXT),)
    assert breakdown.ignored == 1


def test_bucket_lines_truncates_the_echoed_oversized_line_to_120_chars_plus_ellipsis():
    huge_line = "[PERF] checkout: " + ("9" * 20000) + "ms"
    breakdown = bucket_lines([huge_line])
    assert len(breakdown.parse_failures) == 1
    echoed, reason = breakdown.parse_failures[0]
    assert reason == REASON_OVERSIZED
    assert echoed == huge_line[:120] + "…"
    assert len(echoed) == 121


def test_bucket_lines_empty_input_returns_a_zeroed_breakdown():
    breakdown = bucket_lines([])
    assert breakdown.parsed == ()
    assert breakdown.mark_start_without_end == 0
    assert breakdown.perf_meta == 0
    assert breakdown.parse_failures == ()
    assert breakdown.ignored == 0
