"""Contract test for `contracts/markers_doctor_v1.build_doctor_payload`
(SKILL rule 8: "A contract test MUST fail on any `--json` shape change
without a `schema_version` bump."). Asserts required keys + types for the
`markers_doctor_v1` payload — mirrors `test_init_v1_contract.py` /
`test_json_v1_contract.py`'s per-item shape pinning. Covers BOTH `doctor`
modes (`"line"` and `"stdin"`) per markers-command spec "Doctor --json
Payload": "ONE coherent ... schema shape ... not two competing shapes".
markers-command Phase 2 task 2.4.

Reuses the SAME `REASON_*` vocabulary `classify_line` owns
(`perf.adapters.markers_adb_logcat`) rather than redefining reason
strings, per markers-command spec "Shared Line-Classification Function".
"""

from __future__ import annotations

import json

from perf.adapters.markers_adb_logcat import (
    REASON_INVALID_JSON,
    REASON_INVALID_VALUE,
    REASON_MALFORMED_TEXT,
    REASON_OVERSIZED,
)
from perf.contracts.markers_doctor_v1 import SCHEMA_VERSION, build_doctor_payload
from perf.domain.model import Marker

_REQUIRED_KEYS_AND_TYPES = {
    "schema_version": int,
    "mode": str,
    "input_summary": dict,
    "breakdown": dict,
    "coverage_ok": bool,
    "diagnostic": (str, type(None)),
}

# Nested shapes, pinned EXACTLY (SKILL rule 8: any shape change — additive
# included — must fail without a `schema_version` bump).
_INPUT_SUMMARY_KEYS = {"lines_scanned"}
_BREAKDOWN_KEYS = {"parsed", "mark_start_without_end", "perf_meta", "parse_failures", "ignored"}
_PARSED_ITEM_KEYS = {"name", "value", "unit"}
_PARSE_FAILURE_ITEM_KEYS = {"line", "reason"}


def _sample_payload(*, mode: str = "line", **overrides) -> dict:
    defaults = {
        "mode": mode,
        "lines_scanned": 1,
        "parsed": [Marker(name="Home_render", value=128.0, unit="ms")],
        "mark_start_without_end": 0,
        "perf_meta": 0,
        "parse_failures": [("[PERF] bad: nope", REASON_MALFORMED_TEXT)],
        "ignored": 0,
        "coverage_ok": True,
        "diagnostic": None,
    }
    defaults.update(overrides)
    return build_doctor_payload(**defaults)


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = _sample_payload()
    assert payload["schema_version"] == 1


def test_required_keys_present_with_correct_types():
    payload = _sample_payload()
    for key, expected_type in _REQUIRED_KEYS_AND_TYPES.items():
        assert key in payload, f"missing required markers_doctor_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type), (
            f"{key!r} has type {type(payload[key])!r}, expected {expected_type!r}"
        )


def test_mode_line_produces_the_same_shape_as_mode_stdin():
    line_payload = _sample_payload(mode="line")
    stdin_payload = _sample_payload(mode="stdin")
    assert line_payload["schema_version"] == stdin_payload["schema_version"]
    assert set(line_payload.keys()) == set(stdin_payload.keys())
    assert set(line_payload["breakdown"].keys()) == set(stdin_payload["breakdown"].keys())
    assert line_payload["mode"] == "line"
    assert stdin_payload["mode"] == "stdin"


def test_input_summary_carries_lines_scanned():
    payload = _sample_payload(lines_scanned=42)
    assert payload["input_summary"]["lines_scanned"] == 42


def test_parsed_items_pin_name_value_unit_shape():
    payload = _sample_payload(parsed=[Marker(name="cold_start", value=812.0, unit="ms")])
    parsed = payload["breakdown"]["parsed"]
    assert len(parsed) == 1
    assert set(parsed[0].keys()) == _PARSED_ITEM_KEYS
    assert parsed[0] == {"name": "cold_start", "value": 812.0, "unit": "ms"}


def test_parse_failures_pin_line_and_reason_shape():
    payload = _sample_payload(
        parse_failures=[
            ("[PERF] a: nope", REASON_MALFORMED_TEXT),
            ("[PERF] {bad json", REASON_INVALID_JSON),
            ('[PERF] {"name":"x","value":-1}', REASON_INVALID_VALUE),
        ]
    )
    failures = payload["breakdown"]["parse_failures"]
    assert len(failures) == 3
    for entry in failures:
        assert set(entry.keys()) == _PARSE_FAILURE_ITEM_KEYS
    assert failures[0] == {"line": "[PERF] a: nope", "reason": REASON_MALFORMED_TEXT}


def test_oversized_lines_use_the_same_reason_vocabulary():
    payload = _sample_payload(parse_failures=[("x" * 5000, REASON_OVERSIZED)])
    assert payload["breakdown"]["parse_failures"][0]["reason"] == REASON_OVERSIZED


def test_category_counts_round_trip():
    payload = _sample_payload(mark_start_without_end=2, perf_meta=3, ignored=4)
    breakdown = payload["breakdown"]
    assert breakdown["mark_start_without_end"] == 2
    assert breakdown["perf_meta"] == 3
    assert breakdown["ignored"] == 4


def test_coverage_ok_and_diagnostic_round_trip():
    payload = _sample_payload(coverage_ok=False, diagnostic="saw 1 line but 0 markers")
    assert payload["coverage_ok"] is False
    assert payload["diagnostic"] == "saw 1 line but 0 markers"


def test_nothing_parsed_is_still_a_well_shaped_payload():
    payload = _sample_payload(
        mode="stdin",
        lines_scanned=0,
        parsed=[],
        parse_failures=[],
        coverage_ok=True,
        diagnostic=None,
    )
    assert payload["breakdown"]["parsed"] == []
    assert payload["breakdown"]["parse_failures"] == []
    assert payload["coverage_ok"] is True
    assert payload["diagnostic"] is None


def test_payload_is_json_serializable_and_lossless():
    payload = _sample_payload()
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard: any top-level or nested key
    addition/removal/rename without a `SCHEMA_VERSION` bump fails this
    test (exact-set pinning, matching `test_json_v1_contract.py`'s
    pattern)."""
    payload = _sample_payload()
    assert set(payload.keys()) == set(_REQUIRED_KEYS_AND_TYPES)
    assert set(payload["input_summary"].keys()) == _INPUT_SUMMARY_KEYS
    assert set(payload["breakdown"].keys()) == _BREAKDOWN_KEYS
    for item in payload["breakdown"]["parsed"]:
        assert set(item.keys()) == _PARSED_ITEM_KEYS
    for item in payload["breakdown"]["parse_failures"]:
        assert set(item.keys()) == _PARSE_FAILURE_ITEM_KEYS
    assert payload["schema_version"] >= 1
