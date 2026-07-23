"""Contract test for `contracts/compare_v1.build_compare_payload` (SKILL
rule 8: "A contract test MUST fail on any `--json` shape change without a
`schema_version` bump."). Asserts required keys + types for both the
per-metric verdicts and the config sanity label (`calibration`), and that
NO secret ever leaks into the payload. PR-C task 3.1/3.3a.
"""

from __future__ import annotations

import json

from perf.contracts.compare_v1 import SCHEMA_VERSION, build_compare_payload
from perf.domain.calibration import CalibrationReport, MetricCalibration
from perf.domain.model import CompareResult, Verdict

_REQUIRED_VERDICT_KEYS_AND_TYPES = {
    "metric": str,
    "unit": str,
    "direction": str,
    "latest_value": (float, int, type(None)),
    "baseline_value": (float, int, type(None)),
    "delta_pct": (float, int),
    "threshold_pct": (float, int),
    "floor": (float, int),
    "status": str,
    "sample_n": int,
    "baseline_commit_n": int,
}

_REQUIRED_CALIBRATION_KEYS_AND_TYPES = {
    "status": str,
    "runs_flagged": int,
    "runs_total": int,
    "metrics": list,
}


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
        ),
        Verdict(
            metric_name="fps_avg",
            delta_pct=0.0,
            threshold_pct=5.0,
            status="insufficient-data",
            latest_value=None,
            baseline_value=None,
            unit="fps",
            sample_n=0,
            baseline_commit_n=0,
            series=(),
            floor=2.0,
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
    payload = build_compare_payload(_sample_result())
    assert payload["schema_version"] == 1


def test_required_verdict_keys_present_with_correct_types():
    payload = build_compare_payload(_sample_result())
    assert len(payload["verdicts"]) == 2
    for verdict_payload in payload["verdicts"]:
        for key, expected_type in _REQUIRED_VERDICT_KEYS_AND_TYPES.items():
            assert key in verdict_payload, f"missing required verdict key: {key!r}"
            assert isinstance(verdict_payload[key], expected_type), (
                f"{key!r} has type {type(verdict_payload[key])!r}, expected {expected_type!r}"
            )


def test_required_calibration_keys_present_with_correct_types():
    payload = build_compare_payload(_sample_result())
    calibration_payload = payload["calibration"]
    for key, expected_type in _REQUIRED_CALIBRATION_KEYS_AND_TYPES.items():
        assert key in calibration_payload, f"missing required calibration key: {key!r}"
        assert isinstance(calibration_payload[key], expected_type), (
            f"{key!r} has type {type(calibration_payload[key])!r}, expected {expected_type!r}"
        )
    assert calibration_payload["status"] == "reasonable"
    assert calibration_payload["runs_flagged"] == 2
    assert calibration_payload["runs_total"] == 12


def test_sanity_label_present_in_json_never_changes_shape():
    """spec 'Label never changes exit code or verdicts' + task 3.3a: the
    sanity label surfaces in `--json` alongside the verdicts, unaffected
    by them."""
    payload = build_compare_payload(_sample_result())
    assert payload["calibration"]["status"] in {"reasonable", "too-loose", "too-strict", "insufficient-data"}
    # Presence of the label never mutates a per-metric verdict's status.
    statuses = {v["status"] for v in payload["verdicts"]}
    assert statuses == {"regression", "insufficient-data"}


def test_direction_reflects_metric_direction_metadata():
    payload = build_compare_payload(_sample_result())
    by_metric = {v["metric"]: v for v in payload["verdicts"]}
    assert by_metric["checkout"]["direction"] == "lower-is-better"
    assert by_metric["fps_avg"]["direction"] == "higher-is-better"


def test_payload_is_json_serializable_and_lossless():
    payload = build_compare_payload(_sample_result())
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_no_secret_ever_appears_in_payload():
    payload = build_compare_payload(_sample_result())
    serialized = json.dumps(payload).lower()
    for forbidden in ("password", "secret", "--env", "token"):
        assert forbidden not in serialized, f"forbidden term leaked into --json: {forbidden!r}"


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard: removing/renaming a required key
    without bumping `SCHEMA_VERSION` fails this test."""
    payload = build_compare_payload(_sample_result())
    assert set(_REQUIRED_CALIBRATION_KEYS_AND_TYPES).issubset(payload["calibration"].keys())
    for verdict_payload in payload["verdicts"]:
        assert set(_REQUIRED_VERDICT_KEYS_AND_TYPES).issubset(verdict_payload.keys())
    assert payload["schema_version"] >= 1
