"""Contract test for `contracts/compare_all_v1.build_compare_all_payload`
(SKILL rule 8: "A contract test MUST fail on any `--json` shape change
without a `schema_version` bump."). Pins the multi-flow envelope shape with
EXACT-set key assertions at EVERY level, mirroring
`test_budget_check_v1`'s pattern:

- a top-level `{schema_version, flows}` envelope
- each `flows[]` entry is EITHER a `{flow, result}` pair whose `result` is
  the EXACT per-flow `compare_v1` payload, OR a `{flow, error}` pair for a
  skipped no-history flow (`error == "no-history"`)

It never re-validates the nested `compare_v1` shape key-by-key — that is
`compare_v1`'s own contract test's job; here we assert the nested payload is
byte-identical to what `compare_v1.build_compare_payload` produces, so the
two contracts stay decoupled but provably consistent.
"""

from __future__ import annotations

import json

from perf.contracts.compare_all_v1 import SCHEMA_VERSION, build_compare_all_payload
from perf.contracts.compare_v1 import build_compare_payload
from perf.domain.calibration import CalibrationReport, MetricCalibration
from perf.domain.model import CompareResult, Verdict

_TOP_LEVEL_KEYS = {"schema_version", "flows"}
_RESULT_ENTRY_KEYS = {"flow", "result"}
_ERROR_ENTRY_KEYS = {"flow", "error"}


def _sample_result() -> CompareResult:
    verdicts = (
        Verdict(
            metric_name="checkout",
            delta_pct=20.0,
            threshold_pct=5.0,
            status="regression",
            latest_value=120.0,
            baseline_value=100.0,
            unit="ms",
            sample_n=3,
            baseline_commit_n=5,
            series=(100.0, 102.0, 98.0, 120.0),
            floor=5.0,
            higher_is_better=False,
        ),
    )
    calibration = CalibrationReport(
        metrics=(
            MetricCalibration(
                metric_name="checkout",
                status="reasonable",
                flagged_count=2,
                total_count=12,
                max_abs=30.0,
                noise_pct=1.2,
            ),
        ),
        status="reasonable",
        runs_flagged=2,
        runs_total=12,
    )
    return CompareResult(verdicts=verdicts, calibration=calibration)


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = build_compare_all_payload([("checkout", _sample_result())])
    assert payload["schema_version"] == 1


def test_top_level_keys_are_exactly_the_envelope_shape():
    payload = build_compare_all_payload([("checkout", _sample_result())])
    assert set(payload.keys()) == _TOP_LEVEL_KEYS
    assert isinstance(payload["flows"], list)


def test_flows_preserve_caller_order():
    payload = build_compare_all_payload(
        [
            ("login", _sample_result()),
            ("checkout", None),
            ("search", _sample_result()),
        ]
    )
    assert [entry["flow"] for entry in payload["flows"]] == ["login", "checkout", "search"]


def test_result_entry_wraps_the_exact_compare_v1_payload():
    result = _sample_result()
    payload = build_compare_all_payload([("checkout", result)])
    entry = payload["flows"][0]
    assert set(entry.keys()) == _RESULT_ENTRY_KEYS
    assert entry["flow"] == "checkout"
    # Byte-identical to compare_v1's own payload — the multi-flow envelope
    # embeds it verbatim, it does not re-serialize the fields itself.
    assert entry["result"] == build_compare_payload(result)


def test_no_history_flow_becomes_an_error_entry():
    payload = build_compare_all_payload([("checkout", None)])
    entry = payload["flows"][0]
    assert set(entry.keys()) == _ERROR_ENTRY_KEYS
    assert entry["flow"] == "checkout"
    assert entry["error"] == "no-history"


def test_mixed_present_and_skipped_flows():
    payload = build_compare_all_payload([("checkout", _sample_result()), ("login", None)])
    by_flow = {entry["flow"]: entry for entry in payload["flows"]}
    assert "result" in by_flow["checkout"]
    assert by_flow["login"] == {"flow": "login", "error": "no-history"}


def test_payload_is_json_serializable_and_lossless():
    payload = build_compare_all_payload([("checkout", _sample_result()), ("login", None)])
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_no_secret_ever_appears_in_payload():
    payload = build_compare_all_payload([("checkout", _sample_result())])
    serialized = json.dumps(payload).lower()
    for forbidden in ("password", "secret", "--env", "token"):
        assert forbidden not in serialized, f"forbidden term leaked into --json: {forbidden!r}"


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard with exact-set pinning at every level
    (SKILL rule 8: ANY shape change — additive included — must fail without
    a `schema_version` bump)."""
    payload = build_compare_all_payload([("checkout", _sample_result()), ("login", None)])
    assert set(payload.keys()) == _TOP_LEVEL_KEYS
    for entry in payload["flows"]:
        assert set(entry.keys()) in (_RESULT_ENTRY_KEYS, _ERROR_ENTRY_KEYS)
    assert payload["schema_version"] >= 1
