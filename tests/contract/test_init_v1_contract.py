"""Contract test for `contracts/init_v1.build_init_payload` (SKILL rule 8:
"A contract test MUST fail on any `--json` shape change without a
`schema_version` bump."). Asserts required keys + types for the `init_v1`
payload — mirrors `test_compare_v1_contract.py`. `init-command` PR-A task
1.10.
"""

from __future__ import annotations

import json

from perf.contracts.init_v1 import SCHEMA_VERSION, build_init_payload

_REQUIRED_KEYS_AND_TYPES = {
    "schema_version": int,
    "config_path": str,
    "bundle_id": (str, type(None)),
    "bundle_id_source": str,
    "flows_added": list,
    "flows_skipped": list,
    "flows_total": int,
    "appid_conflict": (list, type(None)),
}


def _sample_payload() -> dict:
    return build_init_payload(
        config_path="perf.toml",
        bundle_id="com.example.app",
        bundle_id_source="detected",
        flows_added=["checkout", "login"],
        flows_skipped=[("existing", "exists")],
        flows_total=3,
        appid_conflict=None,
    )


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = _sample_payload()
    assert payload["schema_version"] == 1


def test_required_keys_present_with_correct_types():
    payload = _sample_payload()
    for key, expected_type in _REQUIRED_KEYS_AND_TYPES.items():
        assert key in payload, f"missing required init_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type), (
            f"{key!r} has type {type(payload[key])!r}, expected {expected_type!r}"
        )


def test_bundle_id_source_reflects_the_detection_origin():
    for source in ("detected", "flag", "prompt", "none"):
        payload = build_init_payload(
            config_path="perf.toml",
            bundle_id=None,
            bundle_id_source=source,
            flows_added=[],
            flows_skipped=[],
            flows_total=0,
            appid_conflict=None,
        )
        assert payload["bundle_id_source"] == source


def test_flows_skipped_entries_have_name_and_reason():
    payload = build_init_payload(
        config_path="perf.toml",
        bundle_id=None,
        bundle_id_source="none",
        flows_added=[],
        flows_skipped=[("login", "exists")],
        flows_total=1,
        appid_conflict=None,
    )
    assert payload["flows_skipped"] == [{"name": "login", "reason": "exists"}]


def test_appid_conflict_defaults_to_null():
    payload = build_init_payload(
        config_path="perf.toml",
        bundle_id="com.example.app",
        bundle_id_source="detected",
        flows_added=["login"],
        flows_skipped=[],
        flows_total=1,
    )
    assert payload["appid_conflict"] is None


def test_appid_conflict_lists_the_conflicting_values():
    payload = build_init_payload(
        config_path="perf.toml",
        bundle_id=None,
        bundle_id_source="none",
        flows_added=["a", "b"],
        flows_skipped=[],
        flows_total=2,
        appid_conflict=["com.example.app", "com.other.app"],
    )
    assert payload["appid_conflict"] == ["com.example.app", "com.other.app"]


def test_payload_is_json_serializable_and_lossless():
    payload = _sample_payload()
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard: removing/renaming a required key
    without bumping `SCHEMA_VERSION` fails this test."""
    payload = _sample_payload()
    assert set(_REQUIRED_KEYS_AND_TYPES).issubset(payload.keys())
    assert payload["schema_version"] >= 1
