"""Contract test for `contracts/budget_check_v1.build_payload` (design §8,
decision D1, task 2.3). Pins the FLATTENED shape INDEPENDENTLY of
`compare_v1`'s contract test (SKILL rule 8: "A contract test MUST fail on
any `--json` shape change without a `schema_version` bump."):

- a top-level `gate_status` (`"pass" | "fail" | "skipped"`)
- a FLAT `verdicts[]` list where each entry carries compare's per-metric
  verdict fields PLUS an added `gated: bool`
- `series_points`/`calibration` are DELIBERATELY ABSENT — the gate contract
  stays lean and gate-first (render-time chart/label concerns, not the
  machine gate contract)
- the payload is NOT nested under a `compare` key

This test never imports or delegates to `compare_v1`'s contract test — it
validates ONLY against its own schema, per design §8/§14 (D1).
"""

from __future__ import annotations

import json

from perf.contracts.budget_check_v1 import SCHEMA_VERSION, build_payload
from perf.domain import budget
from perf.domain.calibration import CalibrationReport, MetricCalibration
from perf.domain.model import CompareResult, Verdict
from perf.domain.regression import STATUS_REGRESSION, STATUS_STABLE

_TOP_LEVEL_KEYS = {"schema_version", "gate_status", "strict", "offending_metrics", "verdicts"}

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
    "gated": bool,
    "sample_n": int,
    "baseline_commit_n": int,
}

_CALIBRATION = CalibrationReport(
    metrics=(
        MetricCalibration(
            metric_name="checkout",
            status="reasonable",
            flagged_count=0,
            total_count=3,
            max_abs=1.0,
            noise_pct=1.0,
        ),
    ),
    status="reasonable",
    runs_flagged=0,
    runs_total=3,
)


def _sample_budget_verdict():
    verdicts = (
        Verdict(
            metric_name="checkout",
            delta_pct=20.0,
            threshold_pct=5.0,
            status=STATUS_REGRESSION,
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
            status=STATUS_STABLE,
            latest_value=60.0,
            baseline_value=60.0,
            unit="fps",
            sample_n=3,
            baseline_commit_n=5,
            series=(60.0, 60.0, 60.0),
            floor=2.0,
        ),
    )
    result = CompareResult(verdicts=verdicts, calibration=_CALIBRATION)
    return budget.evaluate(result, strict=False)


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = build_payload(_sample_budget_verdict())
    assert payload["schema_version"] == 1


def test_top_level_keys_are_exactly_the_flat_gate_shape():
    payload = build_payload(_sample_budget_verdict())
    assert set(payload.keys()) == _TOP_LEVEL_KEYS


def test_gate_status_and_strict_and_offending_metrics_reflect_the_verdict():
    payload = build_payload(_sample_budget_verdict())
    assert payload["gate_status"] == budget.GATE_FAIL
    assert payload["strict"] is False
    assert payload["offending_metrics"] == ["checkout"]


def test_verdicts_is_a_flat_list_not_nested_under_a_compare_key():
    payload = build_payload(_sample_budget_verdict())
    assert "compare" not in payload
    assert isinstance(payload["verdicts"], list)
    assert len(payload["verdicts"]) == 2


def test_each_verdict_entry_carries_gated_plus_compares_per_metric_fields():
    payload = build_payload(_sample_budget_verdict())
    for verdict_payload in payload["verdicts"]:
        for key, expected_type in _REQUIRED_VERDICT_KEYS_AND_TYPES.items():
            assert key in verdict_payload, f"missing required verdict key: {key!r}"
            assert isinstance(verdict_payload[key], expected_type), (
                f"{key!r} has type {type(verdict_payload[key])!r}, expected {expected_type!r}"
            )
    by_metric = {v["metric"]: v for v in payload["verdicts"]}
    assert by_metric["checkout"]["gated"] is True
    assert by_metric["fps_avg"]["gated"] is False


def test_series_points_and_calibration_are_absent_pinned_exclusions():
    payload = build_payload(_sample_budget_verdict())
    assert "calibration" not in payload
    for verdict_payload in payload["verdicts"]:
        assert "series_points" not in verdict_payload
        assert "series" not in verdict_payload


def test_gate_banner_never_appears_in_json_payload():
    """Pinned per D1/spec 'Gate banner never appears in --json': no pretty
    banner text or ANSI/formatting artifact ever leaks into the payload,
    for any gate status."""
    for status_fn in (
        lambda: budget.evaluate(
            CompareResult(
                verdicts=(
                    Verdict(
                        metric_name="checkout",
                        delta_pct=1.0,
                        threshold_pct=5.0,
                        status=STATUS_STABLE,
                    ),
                ),
                calibration=_CALIBRATION,
            )
        ),
        _sample_budget_verdict,
    ):
        payload = build_payload(status_fn())
        serialized = json.dumps(payload).upper()
        for banner_text in ("GATE: PASS", "GATE: FAIL", "GATE: SKIPPED", "\x1b["):
            assert banner_text not in serialized


def test_payload_is_json_serializable():
    payload = build_payload(_sample_budget_verdict())
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard, independent of `compare_v1`'s own
    contract test (design §8/§14: 'no coupling, no retrofit'): removing/
    renaming a required top-level or per-verdict key without bumping
    `SCHEMA_VERSION` fails this test."""
    payload = build_payload(_sample_budget_verdict())
    assert set(payload.keys()) == _TOP_LEVEL_KEYS
    for verdict_payload in payload["verdicts"]:
        assert set(_REQUIRED_VERDICT_KEYS_AND_TYPES).issubset(verdict_payload.keys())
    assert payload["schema_version"] >= 1
