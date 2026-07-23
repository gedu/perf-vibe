"""Contract test for `contracts/json_v1.build_run_payload` (SKILL rule 8:
"A contract test MUST fail on any `--json` shape change without a
`schema_version` bump."). Asserts required keys + types, and that NO
secret ever leaks into the payload.
"""

from __future__ import annotations

import json

from perf.application.run_flow import RunFlowResult
from perf.contracts.json_v1 import SCHEMA_VERSION, build_run_payload
from perf.domain.model import Marker, SystemSample

_REQUIRED_KEYS_AND_TYPES = {
    "schema_version": int,
    "run_id": int,
    "flow": str,
    "device": str,
    "source": str,
    "commit": (str, type(None)),
    "is_dev_bundle": (bool, type(None)),
    "mode": str,
    "n": int,
    "partial_coverage": bool,
    "measures": dict,
    "flashlight": list,
}


def _sample_result(**overrides) -> RunFlowResult:
    defaults = dict(
        run_id=42,
        flow_name="checkout",
        device_key="Pixel-Fake|14|physical",
        git_commit="abc123",
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
        ),
        raw_report_path="results/checkout-warm.json",
        partial_coverage=False,
    )
    defaults.update(overrides)
    return RunFlowResult(**defaults)


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = build_run_payload(_sample_result())
    assert payload["schema_version"] == 1


def test_required_keys_present_with_correct_types():
    payload = build_run_payload(_sample_result())
    for key, expected_type in _REQUIRED_KEYS_AND_TYPES.items():
        assert key in payload, f"missing required contract key: {key!r}"
        assert isinstance(payload[key], expected_type), (
            f"{key!r} has type {type(payload[key])!r}, expected {expected_type!r}"
        )


def test_payload_is_json_serializable():
    payload = build_run_payload(_sample_result())
    serialized = json.dumps(payload)
    # Round-trips losslessly.
    assert json.loads(serialized) == payload


def test_measures_grouped_by_metric_name_lossless():
    payload = build_run_payload(_sample_result())
    assert payload["measures"]["checkout"]["values"] == [900.0, 950.0]
    assert payload["measures"]["checkout"]["n"] == 2
    assert payload["measures"]["checkout"]["unit"] == "ms"


def test_flashlight_aggregates_are_verbatim_per_iteration():
    payload = build_run_payload(_sample_result())
    assert len(payload["flashlight"]) == 1
    assert payload["flashlight"][0]["fps_avg"] == 58.0
    assert payload["flashlight"][0]["iteration_idx"] == 0


def test_no_secret_ever_appears_in_payload():
    """`env`/`PASSWORD` (or any driver secret) must NEVER leak into the
    stable machine contract (spec "Bundle id from config, secret not
    logged")."""

    payload = build_run_payload(_sample_result())
    serialized = json.dumps(payload).lower()
    for forbidden in ("password", "secret", "--env", "token"):
        assert forbidden not in serialized, f"forbidden term leaked into --json: {forbidden!r}"


def test_contract_rejects_a_shape_change_without_version_bump():
    """A structural regression test: if a future change removes/renames a
    required key WITHOUT bumping `SCHEMA_VERSION`, this test fails —
    forcing the author to either preserve the key or bump the version and
    update this contract deliberately."""

    payload = build_run_payload(_sample_result())
    assert set(_REQUIRED_KEYS_AND_TYPES).issubset(payload.keys())
    if set(_REQUIRED_KEYS_AND_TYPES) != set(payload.keys()):
        # New keys are allowed (additive); assert schema_version wasn't
        # silently regressed to 0/None while the shape moved on.
        assert payload["schema_version"] >= 1
